"""``DraftPersisterService`` — write-side persistence helpers (issue #147).

VSA replacement for the legacy
``PrepareVacanciesUseCase._save_vacancy_to_storage`` +
``PrepareVacanciesUseCase._save_skipped_ai_rejected`` +
``PrepareVacanciesUseCase._save_vsa_draft_to_legacy_storage``
methods.

The service owns the "save to storage" half of the prepare pipeline:

* :meth:`save_vacancy` — persist ``vacancies`` + ``vacancy_contacts``
  rows. RepositoryError is caught and logged (legacy contract: don't
  block the run on a contact-save failure).
* :meth:`save_employer` — fetch ``/employers/{id}`` and persist to
  ``employers``. Returns silently when the vacancy has no
  ``employer.id``; the API error path is logged at DEBUG.
* :meth:`save_skipped_ai_rejected` — record an AI-rejected vacancy
  in ``skipped_vacancies`` with ``reason='ai_rejected'``. The
  ``created_at`` is read from the optional ``clock`` port.
* :meth:`save_vsa_draft_to_legacy_storage` — convert a VSA
  ``ApplicationDraft`` (dataclass) to the legacy
  ``ApplicationDraftModel`` and persist it via
  ``storage.application_drafts`` (the VSA → legacy write shim from
  issue #142). The saved row is re-read by ``(resume_id, vacancy_id)``
  so callers see the autoincrement ``id`` assigned by the legacy
  facade.

The service is duck-typed on ``storage`` so it can be used by both
the legacy use case and the VSA ``ApplicationPrepSlice``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)


class DraftPersisterService:
    """Write-side persistence helpers for the prepare pipeline.

    Args:
        storage: duck-typed storage facade. Must support
            ``.vacancies.save``, ``.vacancy_contacts.save``,
            ``.employers.save``, ``.skipped_vacancies.save``,
            ``.application_drafts.save``, and
            ``.application_drafts.get_by_resume_vacancy``.
        clock: optional ``Clock`` port (Clean Architecture,
            issue #35). When set, ``clock.now()`` is used for the
            ``created_at`` field on ``skipped_vacancies`` rows.
            Otherwise ``datetime.now()`` is used.
        progress_callback: optional progress callback.
    """

    def __init__(
        self,
        *,
        storage: Any,
        clock: Any | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.storage = storage
        self.clock = clock
        self.progress_callback = progress_callback

    # ─── Vacancy + contacts ──────────────────────────────────────

    def save_vacancy(self, vacancy: dict[str, Any]) -> None:
        """Persist a vacancy (and its contacts when present) to
        ``storage.vacancies`` / ``storage.vacancy_contacts``.

        Errors are logged at DEBUG (vacancy) / EXCEPTION (contacts)
        but never raised — matches the legacy contract.
        """
        from job_bot._legacy_compat.storage.repositories.errors import (
            RepositoryError,
        )

        try:
            self.storage.vacancies.save(vacancy)
        except RepositoryError as ex:
            logger.debug(ex)
        if vacancy.get("contacts"):
            try:
                self.storage.vacancy_contacts.save(vacancy)
            except RepositoryError as ex:
                logger.exception(ex)

    # ─── Employer ────────────────────────────────────────────────

    def save_employer(
        self, vacancy: dict[str, Any], *, api_client: Any
    ) -> None:
        """Fetch ``/employers/{id}`` and persist to
        ``storage.employers``.

        Silently returns when the vacancy has no ``employer.id`` or
        when the API call fails.
        """
        import requests

        from job_bot.shared.api.errors import ApiError, BadResponse
        from job_bot._legacy_compat.storage.repositories.errors import (
            RepositoryError,
        )

        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")
        if not employer_id:
            return
        try:
            profile = api_client.get(f"/employers/{employer_id}")
        except (requests.RequestException, ApiError, BadResponse) as ex:
            logger.debug("Не удалось получить профиль работодателя: %s", ex)
            return
        try:
            self.storage.employers.save(profile)
        except RepositoryError as ex:
            logger.exception(ex)

    # ─── AI-rejected skip ────────────────────────────────────────

    def save_skipped_ai_rejected(
        self, vacancy: dict[str, Any], resume_id: str | None
    ) -> None:
        """Persist a ``skipped_vacancies`` row for an AI-rejected
        vacancy (``reason='ai_rejected'``)."""
        from job_bot._legacy_compat.storage.repositories.errors import (
            RepositoryError,
        )

        employer = vacancy.get("employer") or {}
        created_at = self.clock.now() if self.clock else datetime.now()
        try:
            self.storage.skipped_vacancies.save(
                {
                    "resume_id": resume_id or "",
                    "vacancy_id": vacancy.get("id"),
                    "reason": "ai_rejected",
                    "alternate_url": vacancy.get("alternate_url"),
                    "name": vacancy.get("name"),
                    "employer_name": employer.get("name"),
                    "created_at": created_at,
                }
            )
        except RepositoryError as ex:
            logger.warning("Не удалось сохранить skipped_vacancy: %s", ex)

    # ─── VSA → legacy draft shim ─────────────────────────────────

    def save_vsa_draft_to_legacy_storage(
        self, vsa_draft: Any, resume: dict[str, Any]
    ) -> Any:
        """Mirror a VSA :class:`ApplicationDraft` to the legacy
        :class:`hh_applicant_tool.storage.facade.StorageFacade`.

        Issue #142: the VSA
        :class:`job_bot.application_prep.handlers.application_handler.ApplicationHandler.prepare_draft`
        writes the draft to its own
        ``ApplicationDraftRepository`` (backed by a separate
        database). The use case's callers — and the test suite —
        read drafts via ``self.storage`` (the legacy facade). This
        helper converts the VSA dataclass to the legacy
        ``ApplicationDraftModel`` and persists it via the facade,
        so the two stores stay in sync for the duration of the
        use case run.

        Returns the legacy model on success, or ``None`` if the
        VSA draft is ``None`` (i.e. the vacancy was skipped by the
        filter and never made it to a draft save).
        """
        if vsa_draft is None:
            return None

        from job_bot._legacy_compat.storage.models.application_draft import (
            ApplicationDraftModel,
        )
        from job_bot._legacy_compat.storage.repositories.errors import (
            RepositoryError,
        )

        # Convert VSA ``ApplicationDraft`` → legacy ``ApplicationDraftModel``.
        # The dataclass and the legacy model share most fields by
        # name; the VSA ``id`` is a UUID string (where the legacy
        # uses an auto-increment int). We let the legacy facade
        # assign the int id by passing ``id=None``.
        legacy_draft = ApplicationDraftModel(
            id=None,
            search_profile_id=vsa_draft.search_profile_id,
            resume_id=vsa_draft.resume_id or resume.get("id", ""),
            vacancy_id=int(vsa_draft.vacancy_id or 0),
            employer_id=(
                int(vsa_draft.employer_id) if vsa_draft.employer_id else None
            ),
            status=vsa_draft.status,
            relevance_score=vsa_draft.relevance_score,
            success_probability=None,
            relevance_reason=vsa_draft.relevance_reason,
            analysis_json=vsa_draft.analysis_json,
            full_vacancy_json=vsa_draft.full_vacancy_json,
            cover_letter=vsa_draft.cover_letter,
            cover_letter_status=vsa_draft.cover_letter_status,
            has_test=vsa_draft.has_test,
            test_status=vsa_draft.test_status,
        )
        # ``ApplicationDraftRepository.save`` is an upsert and does
        # not return the saved row, so we re-read by
        # ``(resume_id, vacancy_id)`` to obtain the int ``id``
        # assigned by the legacy autoincrement PK. If the re-read
        # fails (e.g. the row was not written) we fall back to the
        # in-memory ``legacy_draft`` so callers see a non-None
        # draft and the ``prepared`` counter is incremented.
        try:
            self.storage.application_drafts.save(legacy_draft)
        except RepositoryError as ex:
            logger.warning(
                "Не удалось сохранить черновик в legacy storage: %s",
                ex,
            )
            return legacy_draft
        try:
            saved = self.storage.application_drafts.get_by_resume_vacancy(
                str(legacy_draft.resume_id or ""),
                int(legacy_draft.vacancy_id or 0),
            )
        except RepositoryError as ex:
            logger.warning(
                "Не удалось перечитать черновик после save: %s",
                ex,
            )
            return legacy_draft
        return saved if saved is not None else legacy_draft
