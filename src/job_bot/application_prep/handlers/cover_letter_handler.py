"""Cover letter handler - generates cover letters using AI or templates."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import TYPE_CHECKING, Any

import requests

from job_bot.application_prep.models.cover_letter import (
    DEFAULT_LETTER_TEMPLATE,
    CoverLetter,
    CoverLetterCreate,
)
from job_bot.application_prep.repositories.cover_letter_repo import (
    CoverLetterRepository,
)
from job_bot.shared.api.client import HHApiClient
from job_bot.shared.storage.database import Database
from job_bot.shared.utils.text import rand_text, strip_tags

if TYPE_CHECKING:
    from job_bot.shared.ai.client import AIClient
    from job_bot.vacancy_search.ports.vacancy_port import VacancyPort

logger = logging.getLogger(__package__)


class CoverLetterHandler:
    """Handler for cover letter generation.

    Implements CoverLetterPort - generates cover letters using AI or templates,
    and manages cover letter persistence.
    """

    def __init__(
        self,
        database: Database,
        api_client: HHApiClient | None = None,
        ai_client: "AIClient | None" = None,
        *,
        template: str | None = None,
        vacancy_port: "VacancyPort | None" = None,
    ) -> None:
        self._repo = CoverLetterRepository(database)
        self._api_client = api_client
        self._ai_client = ai_client
        self._template = template or DEFAULT_LETTER_TEMPLATE
        self._vacancy_port = vacancy_port

    @property
    def ai_client(self) -> "AIClient | None":
        """Currently configured AI client (``None`` falls back to template).

        Issue #54: the cover-letter AI client is set via this property
        by ``_ApplicationPrepAdapter`` so that ``--use-ai`` is honoured
        even when the slice is memoised at construction time without
        an AI client.
        """
        return self._ai_client

    @ai_client.setter
    def ai_client(self, value: "AIClient | None") -> None:
        self._ai_client = value

    def generate_cover_letter(
        self,
        vacancy: dict[str, Any],
        placeholders: dict[str, Any],
        *,
        resume_analysis: str = "",
        resume: dict[str, Any] | None = None,
        force: bool = False,
        required_by_vacancy: bool = False,
    ) -> str:
        """Generate a cover letter.

        If ai_client is set - uses LLM with full vacancy description.
        Otherwise - uses template with placeholders.

        If both force=False and required_by_vacancy=False - returns empty string
        (application without message).
        """
        if not (force or required_by_vacancy):
            return ""

        if self._ai_client is not None:
            return self._generate_via_ai(
                vacancy,
                placeholders,
                resume_analysis=resume_analysis,
                resume=resume,
            )

        return self._apply_template(placeholders)

    def _apply_template(self, placeholders: dict[str, Any]) -> str:
        """Apply template with placeholders.

        Uses a simple template substitution that supports the
        {opt1|opt2} alternative syntax.
        """
        return rand_text(self._template) % placeholders

    def _generate_via_ai(
        self,
        vacancy: dict[str, Any],
        placeholders: dict[str, Any],
        *,
        resume_analysis: str,
        resume: dict[str, Any] | None,
    ) -> str:
        """Generate letter via LLM. On JSON parse failure, returns raw AI response as fallback."""
        full_vacancy_data = self._fetch_full_vacancy(vacancy)

        ai_context = {
            "job": {
                "title": vacancy.get("name"),
                "employer": (vacancy.get("employer") or {}).get("name"),
                "description": (
                    self._strip_tags(full_vacancy_data.get("description", ""))
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
        if self._ai_client is None:
            return ""
        raw_response = self._ai_client.complete(prompt_msg)
        return _parse_ai_letter_response(raw_response)

    def _fetch_full_vacancy(
        self, vacancy: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Fetch full vacancy data.

        Prefers vacancy_port (issue #33); falls back to api_client.get() (deprecated).
        """
        vacancy_id = vacancy.get("id")
        if vacancy_id is None:
            return None

        if self._vacancy_port is not None:
            try:
                vacancy_obj = self._vacancy_port.get_vacancy_by_hh_id(
                    str(vacancy_id)
                )
                if vacancy_obj is not None:
                    return vacancy_obj.raw_data
                return None
            except sqlite3.Error as ex:
                # vacancy_port is a DB-backed lookup; on failure fall through
                # to the api_client fallback below rather than breaking letter
                # generation.
                logger.warning(
                    "vacancy_port.get_vacancy_by_hh_id(%s) failed: %s",
                    vacancy_id,
                    ex,
                )

        if self._api_client is not None:
            try:
                return self._api_client.get(f"/vacancies/{vacancy_id}")
            except (requests.RequestException, ValueError) as ex:
                # HH API can fail on network errors, non-2xx responses
                # (raise_for_status), or JSON parsing (response.json()).
                logger.warning(
                    "Не удалось получить полную вакансию %s для письма: %s",
                    vacancy_id,
                    ex,
                )
                return None
        return None

    @staticmethod
    def _strip_tags(html: str) -> str:
        """Strip HTML tags from a string."""
        return strip_tags(html)

    # Implementation of CoverLetterPort persistence methods

    def get_cover_letter(self, draft_id: str) -> CoverLetter | None:
        """Get cover letter by draft ID."""
        return self._repo.get_by_draft_id(draft_id)

    def save_cover_letter(self, cover_letter: CoverLetterCreate) -> CoverLetter:
        """Save or update cover letter."""
        return self._repo.save(cover_letter)

    def delete_cover_letter(self, draft_id: str) -> bool:
        """Delete cover letter by draft ID."""
        return self._repo.delete_by_draft_id(draft_id)


def _parse_ai_letter_response(raw_response: str) -> str:
    """Parse JSON response from LLM ({"cover_letter": "..."}); on error - return raw text."""
    try:
        clean_json = re.sub(r"```json\s*|\s*```", "", raw_response).strip()
        letter_data = json.loads(clean_json)
        letter = str(letter_data.get("cover_letter", ""))
        if letter:
            return letter
    except (ValueError, TypeError):
        pass
    return raw_response
