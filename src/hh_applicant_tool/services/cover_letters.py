"""Генерация сопроводительных писем (AI или шаблон).

Извлечено из ``operations/apply_vacancies.py`` (issue #3). Сервис инкапсулирует
выбор стратегии:
- если передан ``ai_client`` — генерируем письмо через LLM;
- иначе используем шаблон с подстановкой плейсхолдеров ``rand_text``.

Используется и из ``apply-vacancies`` (после рефакторинга), и из
``prepare-vacancies`` (issue #5) — последний будет передавать ``ai_client``
для оффлайн-подготовки письма и сохранять результат в
``application_drafts.cover_letter``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..utils.string import rand_text, strip_tags

logger = logging.getLogger(__package__)


# Дефолтный шаблон (тот же, что был в apply_vacancies.Operation)
DEFAULT_LETTER_TEMPLATE = (
    "{Здравствуйте|Добрый день}, меня зовут %(first_name)s. "
    "{Прошу|Предлагаю} рассмотреть {мою кандидатуру|мое резюме «%(resume_title)s»} "
    "на вакансию «%(vacancy_name)s». С уважением, %(first_name)s."
)


class CoverLetterService:
    """Генерация сопроводительного письма (AI или шаблон).

    Attributes:
        api_client: HH API клиент (нужен для подгрузки полного описания
            вакансии при AI-генерации).
        ai_client: ``ChatOpenAI`` с system_prompt для генерации писем или
            ``None`` (тогда используется шаблон ``template``).
        template: шаблон письма с плейсхолдерами ``rand_text``. Если не
            передан — берётся ``DEFAULT_LETTER_TEMPLATE``.
    """

    def __init__(
        self,
        api_client: Any,
        ai_client: Any = None,
        *,
        template: str | None = None,
    ):
        self.api_client = api_client
        self.ai_client = ai_client
        self.template = template or DEFAULT_LETTER_TEMPLATE

    def generate(
        self,
        vacancy: dict[str, Any],
        placeholders: dict[str, Any],
        *,
        resume_analysis: str = "",
        resume: dict[str, Any] | None = None,
        force: bool = False,
        required_by_vacancy: bool = False,
    ) -> str:
        """Возвращает текст письма.

        - Если ``ai_client`` задан — идём через LLM с подгрузкой полного
          описания вакансии.
        - Иначе — шаблон ``self.template`` с подстановкой плейсхолдеров
          ``placeholders``.

        Если и ``force=False`` и ``required_by_vacancy=False`` —
        возвращается пустая строка (отклик без сообщения).
        """
        if not (force or required_by_vacancy):
            return ""

        if self.ai_client is not None:
            return self._generate_via_ai(
                vacancy,
                placeholders,
                resume_analysis=resume_analysis,
                resume=resume,
            )

        return rand_text(self.template) % placeholders

    def _generate_via_ai(
        self,
        vacancy: dict[str, Any],
        placeholders: dict[str, Any],
        *,
        resume_analysis: str,
        resume: dict[str, Any] | None,
    ) -> str:
        """Генерирует письмо через LLM. При сбое парсинга JSON — отдаёт
        сырой ответ AI как fallback."""
        try:
            full_vacancy_data = self.api_client.get(
                f"/vacancies/{vacancy['id']}"
            )
        except Exception as ex:
            logger.warning(
                "Не удалось получить полную вакансию %s для письма: %s",
                vacancy.get("id"),
                ex,
            )
            full_vacancy_data = None

        ai_context = {
            "job": {
                "title": vacancy.get("name"),
                "employer": (vacancy.get("employer") or {}).get("name"),
                "description": (
                    strip_tags(full_vacancy_data.get("description", ""))
                    if full_vacancy_data
                    else ""
                ),
                "key_skills": (
                    [s["name"] for s in full_vacancy_data.get("key_skills", [])]
                    if full_vacancy_data
                    else []
                ),
            },
            "candidate": {
                "first_name": placeholders.get("first_name", "Кандидат"),
                "last_name": placeholders.get("last_name", ""),
                "resume_title": (resume or {}).get("title"),
                "experience_summary": resume_analysis,
            },
        }
        prompt_msg = (
            "Проанализируй данные и напиши сопроводительное письмо:\n"
            + json.dumps(ai_context, ensure_ascii=False, indent=2)
        )
        raw_response = self.ai_client.complete(prompt_msg)
        return _parse_ai_letter_response(raw_response)


def _parse_ai_letter_response(raw_response: str) -> str:
    """Парсит JSON-ответ LLM (``{"cover_letter": "..."}``); при ошибке —
    возвращает сырой текст."""
    try:
        clean_json = re.sub(r"```json\s*|\s*```", "", raw_response).strip()
        letter_data = json.loads(clean_json)
        letter = letter_data.get("cover_letter", "")
        if letter:
            return letter
    except (ValueError, TypeError):
        pass
    return raw_response
