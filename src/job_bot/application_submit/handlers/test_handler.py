"""TestHandler -- vacancy-test answer preparation and submission.

Encapsulates the full test pipeline (issue #3):

1. :meth:`TestHandler.fetch_tests` — parses the ``vacancyTests`` block
   from the response page HTML.
2. :meth:`TestHandler.prepare_answers` — generates a
   ``list[TestAnswer]`` (AI or rule-based fallback). Reusable from
   ``prepare-vacancies`` (issue #5) **without** HTTP submission.
3. :meth:`TestHandler.build_payload` + :meth:`TestHandler.submit_apply`
   — assembles the POST payload for
   ``/applicant/vacancy_response/popup`` and submits it.

Replaces the legacy ``hh_applicant_tool.services.vacancy_tests``
module (deleted with issue #77). The handler now owns the pipeline
end-to-end; the slice no longer depends on legacy services.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import TYPE_CHECKING, Any

from job_bot.application_submit.models.test_answer import TestAnswer

if TYPE_CHECKING:
    from job_bot.application_submit.ports.delay_port import DelayPort

logger = logging.getLogger(__package__)

# Шаблон «вежливого отказа» для вопросов со ссылками.
REFUSAL_WITH_LINK_TEMPLATE = (
    "{{Простите|Извините}, но я не перехожу по "
    "{внешним|сторонним} ссылкам, так как "
    "{опасаюсь взлома|не хочу {быть взломанным|подхватить вирус|"
    "чтобы у меня {со|с банковского} счета украли деньги}}.|"
    "У меня нет времени на заполнение анкет и гуглодоков}"
)

# Задержка перед отправкой payload.
SUBMIT_DELAY_RANGE = (2.0, 3.0)


def _strip_tags(text: str) -> str:
    """Minimal HTML tag stripper — the legacy helper from
    ``hh_applicant_tool.utils.string.strip_tags`` only used re.sub."""
    return re.sub(r"<[^>]+>", "", text or "")


class TestHandler:
    """Vacancy-test answer preparation and submission.

    Attributes:
        session: ``requests.Session`` (used for both fetch and submit).
        ai_client: ``ChatOpenAI`` for answer generation, or ``None``
            (rule-based fallback).
        delay: :class:`DelayPort` for pauses between requests. If not
            provided, falls back to ``time.sleep``.
    """

    def __init__(
        self,
        session: Any,
        ai_client: Any | None = None,
        *,
        delay: "DelayPort | None" = None,
    ) -> None:
        self._session = session
        self._ai_client = ai_client
        self._delay = delay

    # ─── Fetch ────────────────────────────────────────────────────

    def fetch_tests(self, response_url: str) -> dict[str, Any]:
        """Parse the ``vacancyTests`` block from the response page HTML."""
        if hasattr(self._session, "get"):
            response = self._session.get(response_url)
        else:
            response = self._session  # already a Response (for tests)

        marker = ',"vacancyTests":'
        start = response.text.find(marker)
        end = response.text.find(',"counters":', start)

        if -1 in (start, end):
            raise ValueError("tests not found.")

        import json as _json

        try:
            return _json.loads(
                response.text[start + len(marker) : end],
                strict=False,
            )
        except ValueError as ex:
            raise ValueError("Не могу распарсить vacancyTests.") from ex

    # ─── Prepare answers (no HTTP submit) ─────────────────────────

    def prepare_answers(self, test_data: dict[str, Any]) -> list[TestAnswer]:
        """Generate a :class:`TestAnswer` for every task in ``test_data``."""
        return [
            self._prepare_answer_for_task(task)
            for task in test_data.get("tasks", [])
        ]

    def _prepare_answer_for_task(self, task: dict[str, Any]) -> TestAnswer:
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
            if self._ai_client is not None:
                options = "\n".join(
                    f"{s['id']}: {_strip_tags(s['text'])}" for s in solutions
                )
                prompt = (
                    f"Вопрос: {question}\n"
                    f"Варианты:\n{options}\n"
                    "Выбери ID правильного ответа. Пришли только ID."
                )
                ai_answer = self._ai_client.complete(prompt).strip()
                match = re.search(r"\d+", ai_answer)
                selected_solution_id = (
                    match.group(0) if match else str(solutions[0]["id"])
                )
                generated_answer = selected_solution_id
            else:
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
                # `rand_text` is not in scope here — use a stable simple
                # template and pick the first choice (mirrors legacy
                # behaviour when ai_client is None).
                generated_answer = (
                    "Извините, но я не перехожу по внешним ссылкам."
                )
            elif self._ai_client is not None:
                prompt = (
                    "Дай краткий и профессиональный ответ на вопрос: "
                    f"{question}"
                )
                generated_answer = self._ai_client.complete(prompt)
            else:
                generated_answer = "Да"

        return TestAnswer(
            task_id=task_id,
            question=question,
            answer_type=answer_type,
            options_json=options_json,
            generated_answer=generated_answer,
            selected_solution_id=selected_solution_id,
            review_status="generated",
        )

    # ─── Build apply payload + submit ───────────────────────────

    def build_payload(
        self,
        test_data: dict[str, Any],
        answers: list[TestAnswer],
        *,
        vacancy_id: str | int,
        resume_hash: str,
        letter: str = "",
        xsrf_token: str,
    ) -> dict[str, Any]:
        """Build the POST payload for ``/applicant/vacancy_response/popup``.

        ``answers`` — previously generated answers (matched by ``task_id``).
        For tasks without a matching answer we fall back to the rule-based
        default (middle option / "Да") to stay compatible with the legacy
        one-shot flow.
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
        """POST the payload to ``/applicant/vacancy_response/popup``."""
        wait_seconds = random.uniform(*SUBMIT_DELAY_RANGE)
        if self._delay is not None:
            self._delay.sleep(wait_seconds)
        else:
            time.sleep(wait_seconds)

        response = self._session.post(
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


__all__ = ["TestHandler", "SUBMIT_DELAY_RANGE", "REFUSAL_WITH_LINK_TEMPLATE"]
