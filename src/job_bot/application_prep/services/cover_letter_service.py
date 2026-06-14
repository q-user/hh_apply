"""Generating cover letters (AI or template).

.. versionchanged:: 2.0
   Moved from ``hh_applicant_tool.services.cover_letters`` to
   ``job_bot.application_prep.services.cover_letter_service``
   as part of the VSA switchover (issue #77).

Extracted from ``operations/apply_vacancies.py`` (issue #3). The service
encapsulates the strategy choice:

- if ``ai_client`` is given — generate via LLM;
- otherwise use a template with ``rand_text`` placeholder substitution.

Used from ``apply-vacancies`` (after refactoring) and from
``prepare-vacancies`` (issue #5) — the latter passes ``ai_client``
for offline letter preparation and saves the result in
``application_drafts.cover_letter``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from hh_applicant_tool.utils.string import rand_text, strip_tags

if TYPE_CHECKING:
    from hh_applicant_tool.application.ports import (
        VacancyDescriptionFetcherPort,
    )

logger = logging.getLogger(__package__)


DEFAULT_LETTER_TEMPLATE = (
    "{Здравствуйте|Добрый день}, меня зовут %(first_name)s. "
    "{Прошу|Предлагаю} рассмотреть {мою кандидатуру|мое резюме «%(resume_title)s»} "
    "на вакансию «%(vacancy_name)s». С уважением, %(first_name)s."
)


class CoverLetterService:
    """Cover letter generation (AI or template).

    Attributes:
        api_client: HH API client (deprecated, use vacancy_fetcher).
            Needed to load full vacancy description for AI generation.
        vacancy_fetcher: port for loading vacancy description
            (preferred way, issue #33).
        ai_client: ``ChatOpenAI`` with system_prompt for letter generation or
            ``None`` (uses ``template``).
        template: letter template with ``rand_text`` placeholders. If not
            given — uses ``DEFAULT_LETTER_TEMPLATE``.
    """

    def __init__(
        self,
        api_client: Any,
        ai_client: Any = None,
        *,
        template: str | None = None,
        vacancy_fetcher: "VacancyDescriptionFetcherPort | None" = None,
    ):
        self.api_client = api_client
        self.ai_client = ai_client
        self.template = template or DEFAULT_LETTER_TEMPLATE
        self._vacancy_fetcher = vacancy_fetcher

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
        """Return letter text.

        - If ``ai_client`` is set — go via LLM with full vacancy description.
        - Otherwise — template ``self.template`` with placeholder substitution.

        If both ``force=False`` and ``required_by_vacancy=False`` —
        returns empty string (application without message).
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
        """Generate letter via LLM. On JSON parse failure — returns raw AI response as fallback."""
        full_vacancy_data = self._fetch_full_vacancy(vacancy)

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

    def _fetch_full_vacancy(
        self, vacancy: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Load full vacancy description.

        Prefers ``vacancy_fetcher`` (port, issue #33);
        fallback — ``api_client.get()`` (deprecated).
        """
        vacancy_id = vacancy.get("id")
        if vacancy_id is None:
            return None

        if self._vacancy_fetcher is not None:
            try:
                return self._vacancy_fetcher.fetch(str(vacancy_id))
            except Exception as ex:  # noqa: BLE001
                logger.warning(
                    "vacancy_fetcher.fetch(%s) failed: %s",
                    vacancy_id,
                    ex,
                )

        # Deprecated fallback: direct api_client call
        try:
            return self.api_client.get(f"/vacancies/{vacancy_id}")
        except Exception as ex:  # noqa: BLE001
            logger.warning(
                "Не удалось получить полную вакансию %s для письма: %s",
                vacancy_id,
                ex,
            )
            return None


def _parse_ai_letter_response(raw_response: str) -> str:
    """Parse JSON response from LLM (``{"cover_letter": "..."}``); on error — return raw text."""
    try:
        clean_json = re.sub(r"```json\s*|\s*```", "", raw_response).strip()
        letter_data = json.loads(clean_json)
        letter = letter_data.get("cover_letter", "")
        if letter:
            return letter
    except (ValueError, TypeError):
        pass
    return raw_response
