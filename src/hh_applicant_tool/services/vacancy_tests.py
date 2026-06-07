"""Подготовка и отправка ответов на тесты вакансий HH.

Извлечено из ``operations/apply_vacancies.py`` (issue #3). Сервис разделён на
три фазы, чтобы их могли переиспользовать разные операции:

1. :meth:`VacancyTestsService.fetch_tests` — парсит ``vacancyTests`` из HTML
   ответа ``/applicant/vacancy_response``.
2. :meth:`VacancyTestsService.prepare_answers` — генерирует
   ``list[ApplicationTestAnswerModel]`` (AI или rule-based) — может
   вызываться в ``prepare-vacancies`` (issue #5) БЕЗ HTTP-отправки.
3. :meth:`VacancyTestsService.build_apply_payload_from_answers` +
   :meth:`VacancyTestsService.submit_apply` — собирает payload для
   ``/applicant/vacancy_response/popup`` и POSTит его — используется в
   ``apply-vacancies``/``apply-worker``.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

import requests

from .. import utils
from ..storage.models.application_test_answer import (
    ApplicationTestAnswerModel,
)
from ..utils.datatypes import VacancyTest, VacancyTestsData
from ..utils.string import rand_text, strip_tags

logger = logging.getLogger(__package__)


# Шаблон «вежливого отказа» для вопросов со ссылками (legacy)
REFUSAL_WITH_LINK_TEMPLATE = (
    "{{Простите|Извините}, но я не перехожу по "
    "{внешним|сторонним} ссылкам, так как "
    "{опасаюсь взлома|не хочу {быть взломанным|подхватить вирус|"
    "чтобы у меня {со|с банковского} счета украли деньги}}.|"
    "У меня нет времени на заполнение анкет и гуглодоков}"
)

# Задержка перед отправкой payload (legacy)
SUBMIT_DELAY_RANGE = (2.0, 3.0)


def fetch_vacancy_tests(
    session: requests.Session, response_url: str
) -> VacancyTestsData:
    """Парсит блок ``vacancyTests`` из HTML страницы отклика."""
    r = session.get(response_url)

    tests_marker = ',"vacancyTests":'
    start_tests = r.text.find(tests_marker)
    end_tests = r.text.find(',"counters":', start_tests)

    if -1 in (start_tests, end_tests):
        raise ValueError("tests not found.")

    try:
        return utils.json.loads(
            r.text[start_tests + len(tests_marker) : end_tests],
            strict=False,
        )
    except ValueError as ex:
        raise ValueError("Не могу распарсить vacancyTests.") from ex


class VacancyTestsService:
    """Сервис подготовки/отправки ответов на тесты вакансий.

    Attributes:
        session: ``requests.Session`` (используется и для fetch, и для
            submit).
        ai_client: ``ChatOpenAI`` (используется как для генерации ответов,
            так и для выбора option'ов в choice-вопросах) или ``None``
            (тогда rule-based fallback).
    """

    def __init__(self, session: requests.Session, ai_client: Any = None):
        self.session = session
        self.ai_client = ai_client

    # ─── Fetch ────────────────────────────────────────────────────

    def fetch_tests(self, response_url: str) -> VacancyTestsData:
        """Загружает данные тестов по ``response_url``."""
        return fetch_vacancy_tests(self.session, response_url)

    # ─── Prepare answers (без HTTP-отправки) ─────────────────────

    def prepare_answers(
        self, test_data: VacancyTest
    ) -> list[ApplicationTestAnswerModel]:
        """Генерирует ответы на задачи теста. Возвращает
        ``list[ApplicationTestAnswerModel]`` без HTTP-отправки."""
        answers: list[ApplicationTestAnswerModel] = []
        for task in test_data.get("tasks", []):
            answers.append(self._prepare_answer_for_task(task))
        return answers

    def _prepare_answer_for_task(
        self, task: dict[str, Any]
    ) -> ApplicationTestAnswerModel:
        """Подбирает ответ на одну задачу теста."""
        task_id = str(task.get("id"))
        question = (task.get("description") or "").strip()
        solutions = task.get("candidateSolutions") or []

        answer_type: str | None = None
        options_json: list[dict] | None = None
        selected_solution_id: str | None = None
        generated_answer: str

        if solutions:
            answer_type = "choice"
            options_json = [
                {"id": s.get("id"), "text": s.get("text")} for s in solutions
            ]
            if self.ai_client is not None:
                options = "\n".join(
                    f"{s['id']}: {strip_tags(s['text'])}" for s in solutions
                )
                prompt = (
                    f"Вопрос: {question}\n"
                    f"Варианты:\n{options}\n"
                    "Выбери ID правильного ответа. Пришли только ID."
                )
                ai_answer = self.ai_client.complete(prompt).strip()
                match = re.search(r"\d+", ai_answer)
                selected_solution_id = (
                    match.group(0) if match else str(solutions[0]["id"])
                )
                generated_answer = selected_solution_id
            else:
                # Правильный ответ "Да" — частый случай, иначе берём середину
                yes_solution = next(
                    filter(lambda x: x["text"].lower() == "да", solutions),
                    None,
                )
                if yes_solution:
                    selected_solution_id = str(yes_solution["id"])
                else:
                    selected_solution_id = str(
                        solutions[len(solutions) // 2]["id"]
                    )
                generated_answer = selected_solution_id
        else:
            answer_type = "text"
            if "://" in question:
                generated_answer = rand_text(REFUSAL_WITH_LINK_TEMPLATE)
            elif self.ai_client is not None:
                prompt = (
                    f"Дай краткий и профессиональный ответ на вопрос: "
                    f"{question}"
                )
                generated_answer = self.ai_client.complete(prompt)
            else:
                generated_answer = "Да"

        return ApplicationTestAnswerModel(
            draft_id=0,  # будет заполнен при сохранении
            task_id=task_id,
            question=question,
            answer_type=answer_type,
            options_json=options_json,
            generated_answer=generated_answer,
            selected_solution_id=selected_solution_id,
            review_status="generated",
        )

    # ─── Build apply payload + submit ───────────────────────────

    def build_apply_payload_from_answers(
        self,
        test_data: VacancyTest,
        answers: list[ApplicationTestAnswerModel],
        *,
        vacancy_id: str | int,
        resume_hash: str,
        letter: str = "",
        xsrf_token: str,
    ) -> dict[str, Any]:
        """Собирает payload для POST /applicant/vacancy_response/popup.

        ``answers`` — список ранее сгенерированных ответов (порядок
        неважен, связывание по ``task_id``). Для задач, на которые нет
        ответа в ``answers``, выбирается rule-based fallback (середина
        вариантов / "Да"), чтобы сохранить совместимость с legacy-flow,
        где ``_solve_vacancy_test`` генерировал и сохранял в payload
        за один проход.
        """
        answers_by_task = {a.task_id: a for a in answers}
        payload: dict[str, Any] = {
            "_xsrf": xsrf_token,
            "uidPk": test_data["uidPk"],
            "guid": test_data["guid"],
            "startTime": test_data["startTime"],
            "testRequired": test_data["required"],
            "vacancy_id": vacancy_id,
            "resume_hash": resume_hash,
            "ignore_postponed": "true",
            "incomplete": "false",
            "mark_applicant_visible_in_vacancy_country": "false",
            "country_ids": "[]",
            "lux": "true",
            "withoutTest": "no",
            "letter": letter,
        }

        for task in test_data.get("tasks", []):
            field_name = f"task_{task['id']}"
            ans = answers_by_task.get(str(task["id"]))
            solutions = task.get("candidateSolutions") or []

            if solutions:
                if ans and ans.selected_solution_id is not None:
                    payload[field_name] = ans.selected_solution_id
                else:
                    # Fallback — берём середину (или "да")
                    yes_solution = next(
                        filter(lambda x: x["text"].lower() == "да", solutions),
                        None,
                    )
                    payload[field_name] = (
                        str(yes_solution["id"])
                        if yes_solution
                        else str(solutions[len(solutions) // 2]["id"])
                    )
            else:
                if ans and ans.generated_answer is not None:
                    payload[f"{field_name}_text"] = ans.generated_answer
                else:
                    payload[f"{field_name}_text"] = "Да"

        return payload

    def submit_apply(
        self,
        response_url: str,
        payload: dict[str, Any],
        *,
        xsrf_token: str,
    ) -> dict[str, Any]:
        """Отправляет payload на ``/applicant/vacancy_response/popup``.

        Делает паузу ``SUBMIT_DELAY_RANGE`` (legacy-поведение) и
        выставляет Referer/X-Hhtmfrom/X-Xsrftoken.
        """
        # Ожидание перед отправкой
        time.sleep(random.uniform(*SUBMIT_DELAY_RANGE))

        response = self.session.post(
            "https://hh.ru/applicant/vacancy_response/popup",
            data=payload,
            headers={
                "Referer": response_url,
                "X-Hhtmfrom": "vacancy",
                "X-Hhtmsource": "vacancy_response",
                "X-Requested-With": "XMLHttpRequest",
                "X-Xsrftoken": xsrf_token,
            },
        )

        logger.debug(
            "%s %s %d",
            response.request.method,
            response.url,
            response.status_code,
        )
        return response.json()
