"""SkipHandler -- vacancy skip policy (issue #145).

In-slice VSA wrapper for the legacy
``ApplyToVacanciesUseCase._check_vacancy_skips`` /
``_save_skipped_vacancy`` / ``_is_vacancy_already_skipped`` helpers.
Handles every skip condition the apply pipeline cares about:

  * ``do_apply=False`` (limit reached earlier in the loop) -> ``"limit_reached"``
  * vacancy has relations (already responded) -> ``"already_responded"``
  * vacancy is archived -> ``"archived"``
  * vacancy has test and ``command.skip_tests`` is True -> ``"has_test"``
  * vacancy has ``response_url`` (redirect) -> ``"redirected"``
  * vacancy matches the ``excluded_filter`` regex -> ``"excluded"`` + blacklist
  * AI filter rejects the vacancy -> ``"ai_rejected"`` (only when
    ``command.ai_filter`` is set and the per-resume AI client is
    configured)
  * vacancy has been previously skipped -> ``"ai_already_skipped"``

The handler owns the ``StorageFacade.skipped_vacancies`` repository
(15-repo StorageFacade from PR #161). Side-effects (blacklist PUT,
AI-rejected save) are best-effort: failures are logged, not raised,
to keep the apply loop resilient.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__package__)


def _row_to_skipped_vacancy_id(row: Any) -> int | None:
    """Return the ``id`` of a ``skipped_vacancies`` row, or ``None``."""
    try:
        return int(row["id"])
    except (KeyError, TypeError, ValueError):
        return None


class SkipHandler:
    """In-slice skip handler (issue #145).

    Args:
        storage: the legacy :class:`hh_applicant_tool.storage.StorageFacade`
            (or duck-typed equivalent) exposing ``skipped_vacancies``.
        api_client: HH API client (used for the blacklist
            ``PUT /vacancies/blacklisted/{id}`` call on excluded
            vacancies).
        clock: optional :class:`Clock` port for the ``created_at``
            timestamp; falls back to ``datetime.now()``.
    """

    def __init__(
        self,
        storage: Any,
        api_client: Any,
        *,
        clock: Any = None,
    ) -> None:
        self._storage = storage
        self._api_client = api_client
        self._clock = clock

    # ─── Public API ────────────────────────────────────────────

    def check(
        self,
        vacancy: dict[str, Any],
        resume: dict[str, Any],
        do_apply: bool,
        command: Any,
        relevance_handler: Any,
        vacancy_filter_ai: Any,
    ) -> str | None:
        """Check if ``vacancy`` should be skipped.

        Returns the skip reason string or ``None`` if the vacancy
        should be processed. Side-effects (blacklist PUT, skipped
        save) are applied for ``"excluded"`` and ``"ai_rejected"``
        reasons.
        """
        if not do_apply:
            return "limit_reached"
        relations = vacancy.get("relations") or []
        if relations:
            logger.debug(
                "Пропускаем вакансию с откликом: %s",
                vacancy.get("alternate_url"),
            )
            if "got_rejection" in relations:
                logger.debug(
                    "Вы получили отказ от %s", vacancy.get("alternate_url")
                )
            return "already_responded"
        if vacancy.get("archived"):
            logger.debug(
                "Пропускаем вакансию в архиве: %s",
                vacancy.get("alternate_url"),
            )
            return "archived"
        if vacancy.get("has_test") and getattr(command, "skip_tests", False):
            logger.debug(
                "Пропускаю вакансию с тестом %s",
                vacancy.get("alternate_url"),
            )
            return "has_test"
        if vacancy.get("response_url"):
            logger.debug(
                "Пропускаем вакансию %s с перенаправлением: %s",
                vacancy.get("alternate_url"),
                vacancy.get("response_url"),
            )
            return "redirected"
        if self._is_excluded(
            vacancy, getattr(command, "excluded_filter", None)
        ):
            logger.info(
                "Вакансия попала под фильтр: %s",
                vacancy.get("alternate_url"),
            )
            self.save_skipped(vacancy, "excluded_filter", resume.get("id"))
            try:
                self._api_client.put(f"/vacancies/blacklisted/{vacancy['id']}")
            except Exception as ex:  # noqa: BLE001
                logger.warning(
                    "Не удалось добавить вакансию в чёрный список: %s", ex
                )
            logger.info(
                "Вакансия добавлена в черный список: %s",
                vacancy.get("alternate_url"),
            )
            return "excluded"

        # AI filtering
        ai_filter = getattr(command, "ai_filter", None)
        if ai_filter and vacancy_filter_ai is not None:
            if self.is_already_skipped(vacancy, resume.get("id")):
                logger.debug(
                    "Вакансия уже была отклонена ранее: %s",
                    vacancy.get("alternate_url"),
                )
                return "ai_already_skipped"
            ok = self._ask_ai_suitability(relevance_handler, vacancy, ai_filter)
            if not ok:
                logger.info(
                    "Вакансия отклонена AI фильтром (%s): %s",
                    ai_filter,
                    vacancy.get("alternate_url"),
                )
                self.save_skipped(vacancy, "ai_rejected", resume.get("id"))
                return "ai_rejected"
        return None

    def is_already_skipped(
        self, vacancy: dict[str, Any], resume_id: str | None = None
    ) -> bool:
        """Return ``True`` if ``vacancy`` was previously skipped.

        Checks the per-resume records first, then the resume-less
        records (vacancy blacklisted for the whole account).
        """
        try:
            vacancy_id = vacancy["id"]
            repo = self._storage.skipped_vacancies
            if resume_id:
                for _ in repo.find(resume_id=resume_id, vacancy_id=vacancy_id):
                    return True
            for _ in repo.find(resume_id="", vacancy_id=vacancy_id):
                return True
            return False
        except (sqlite3.Error, AttributeError) as ex:
            logger.debug("is_already_skipped lookup failed: %s", ex)
            return False

    def save_skipped(
        self,
        vacancy: dict[str, Any],
        reason: str,
        resume_id: str | None = None,
    ) -> None:
        """Persist a ``skipped_vacancies`` row."""
        try:
            employer = vacancy.get("employer") or {}
            self._storage.skipped_vacancies.save(
                {
                    "resume_id": resume_id or "",
                    "vacancy_id": vacancy["id"],
                    "reason": reason,
                    "alternate_url": vacancy.get("alternate_url"),
                    "name": vacancy.get("name"),
                    "employer_name": employer.get("name"),
                    "created_at": self._now(),
                }
            )
        except (sqlite3.Error, AttributeError) as ex:
            logger.warning(f"Не удалось сохранить пропущенную вакансию: {ex}")

    # ─── Internals ─────────────────────────────────────────────

    def _is_excluded(
        self, vacancy: dict[str, Any], excluded_filter: str | None
    ) -> bool:
        """Lightweight regex-based exclusion check (summary + name).

        Mirrors the legacy ``_is_excluded`` behaviour: the snippet
        (``name`` + ``requirement`` + ``responsibility``) is checked
        first; only on a miss is the full vacancy page fetched.
        """
        if not excluded_filter:
            return False

        snippet = vacancy.get("snippet") or {}
        vacancy_summary = " ".join(
            filter(
                None,
                [
                    vacancy.get("name"),
                    snippet.get("requirement"),
                    snippet.get("responsibility"),
                ],
            )
        )
        logger.debug(vacancy_summary)
        excluded_pat: re.Pattern[str] = re.compile(excluded_filter, re.I)
        if excluded_pat.search(vacancy_summary):
            return True

        # The full-text fetch uses the legacy ``session.get`` (the
        # ``SiteParserPort`` doesn't expose a generic GET). Keep the
        # side-effect best-effort: if it fails, the vacancy passes.
        session = getattr(self, "_session", None)
        if session is None:
            return False
        try:
            r = session.get("https://hh.ru/vacancy/" + str(vacancy["id"]))
            r.raise_for_status()
        except Exception as ex:  # noqa: BLE001
            logger.debug("excluded-filter full-text fetch failed: %s", ex)
            return False
        match = re.search(r'"description": (.*)', r.text)
        if match is None:
            return False
        from job_bot.shared.utils.json_utils import JSONDecoder

        description, _ = JSONDecoder().raw_decode(match.group(1))
        from job_bot.shared.utils.text import strip_tags

        description = strip_tags(cast(str, description))
        logger.debug(description[:2047])
        return bool(excluded_pat.search(description))

    def _ask_ai_suitability(
        self, relevance_handler: Any, vacancy: dict[str, Any], mode: str
    ) -> bool:
        """Wrap ``relevance_handler.is_suitable_*`` and return ``.suitable``."""
        if mode == "heavy":
            return bool(
                relevance_handler.is_suitable_heavy(cast(Any, vacancy)).suitable
            )
        if mode == "light":
            return bool(
                relevance_handler.is_suitable_light(cast(Any, vacancy)).suitable
            )
        raise ValueError(f"Неизвестный режим AI фильтра: {mode}")

    def _now(self) -> Any:
        """Return the current time via the ``Clock`` port (or ``datetime.now()``)."""
        if self._clock is not None:
            return self._clock.now()
        from datetime import datetime

        return datetime.now()


__all__ = ["SkipHandler"]
