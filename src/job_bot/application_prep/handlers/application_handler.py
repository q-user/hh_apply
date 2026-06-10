"""Application draft handler - orchestrates preparation of application drafts."""

from __future__ import annotations

import logging
from typing import Any

from job_bot.application_prep.handlers.cover_letter_handler import (
    CoverLetterHandler,
)
from job_bot.application_prep.handlers.relevance_handler import (
    RelevanceHandler,
)
from job_bot.application_prep.models.application import (
    ApplicationDraft,
    ApplicationDraftCreate,
)
from job_bot.application_prep.models.cover_letter import CoverLetterCreate
from job_bot.application_prep.models.relevance import RelevanceResult
from job_bot.application_prep.repositories.application_repo import (
    ApplicationDraftRepository,
)
from job_bot.application_prep.repositories.cover_letter_repo import (
    CoverLetterRepository,
)
from job_bot.application_prep.repositories.relevance_repo import (
    RelevanceAnalysisRepository,
)
from job_bot.shared.storage.database import Database

logger = logging.getLogger(__package__)


class ApplicationHandler:
    """Handler for application draft preparation.

    Orchestrates:
    1. AI relevance filtering
    2. Cover letter generation
    3. (Optional) Test answer generation
    4. Saving ApplicationDraft

    Implements ApplicationPort.
    """

    def __init__(
        self,
        database: Database,
        relevance: RelevanceHandler | None = None,
        cover_letter: CoverLetterHandler | None = None,
    ) -> None:
        self._draft_repo = ApplicationDraftRepository(database)
        self._cover_letter_repo = CoverLetterRepository(database)
        self._relevance_repo = RelevanceAnalysisRepository(database)
        self.relevance = relevance
        self.cover_letter = cover_letter

    def prepare_draft(
        self,
        *,
        resume: dict[str, Any],
        vacancy: dict[str, Any],
        search_profile_id: str | None = None,
        resume_analysis: str = "",
        ai_filter_mode: str | None = None,
        placeholders: dict[str, Any] | None = None,
        force_message: bool = False,
        response_url: str | None = None,
    ) -> ApplicationDraft | None:
        """Prepare (or update) an application draft.

        Returns:
        - ApplicationDraft with status "prepared" - if vacancy passed AI filter;
        - ApplicationDraft with status "rejected" - if AI filter rejected
          (only score/reason/relevance_reason filled, cover_letter empty);
        - None - if vacancy is not interesting at all.

        Args:
            resume: dict (Resume data)
            vacancy: dict (Vacancy data)
            search_profile_id: optional, for draft.search_profile_id
            resume_analysis: text analysis of resume (used in letter)
            ai_filter_mode: "heavy" / "light" / None
            placeholders: first_name/last_name/resume_title etc. for letter template
            force_message: always generate a letter
            response_url: URL of test page (if vacancy has has_test).
                If not passed and has_test=True - draft is marked
                test_status='manual_required' without generation.
        """
        resume_id = resume.get("id")
        vacancy_id = vacancy.get("id")
        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")

        # 1. AI relevance filtering (if enabled)
        relevance_score: int | None = None
        relevance_reason: str | None = None
        analysis_json: dict | None = None
        status = "prepared"

        relevance_result: RelevanceResult | None = None
        if self.relevance is not None and ai_filter_mode in ("heavy", "light"):
            if ai_filter_mode == "heavy":
                relevance_result = self.relevance.is_suitable_heavy(vacancy)
            else:
                relevance_result = self.relevance.is_suitable_light(vacancy)
            relevance_score = relevance_result.score
            relevance_reason = relevance_result.reason
            analysis_json = _analysis_to_dict(relevance_result)
            if not relevance_result.suitable:
                status = "rejected"

        # If vacancy rejected by AI - save rejected-draft and exit
        if status == "rejected":
            saved = self._draft_repo.save(
                ApplicationDraftCreate(
                    search_profile_id=search_profile_id,
                    resume_id=str(resume_id) if resume_id else "",
                    vacancy_id=int(vacancy_id) if vacancy_id else 0,
                    employer_id=int(employer_id) if employer_id else None,
                    status=status,
                    relevance_score=relevance_score,
                    relevance_reason=relevance_reason,
                    analysis_json=analysis_json,
                    full_vacancy_json=vacancy,
                    cover_letter=None,
                    cover_letter_status=None,
                    has_test=bool(vacancy.get("has_test")),
                    test_status=None,
                )
            )
            # Persist relevance analysis (if we have it)
            if relevance_result is not None:
                self._relevance_repo.save_analysis(saved.id, relevance_result)
            return saved

        # 2. Cover letter generation
        cover_letter: str | None = None
        cover_letter_status: str | None = None
        if self.cover_letter is not None:
            try:
                cover_letter = self.cover_letter.generate_cover_letter(
                    vacancy,
                    placeholders or {},
                    resume_analysis=resume_analysis,
                    resume=resume,
                    force=force_message,
                    required_by_vacancy=bool(
                        vacancy.get("response_letter_required")
                    ),
                )
                cover_letter_status = "generated"
            except Exception as ex:
                logger.warning(
                    "Не удалось сгенерировать сопроводительное письмо: %s",
                    ex,
                )
                cover_letter_status = "failed"

        # 3. Vacancy tests (placeholder - actual test fetching handled by application_submit slice)
        has_test = bool(vacancy.get("has_test"))
        test_status: str | None = None
        if has_test and not response_url:
            test_status = "manual_required"
        # If response_url is provided and tests service is wired, could generate here.
        # For Phase 2 we just mark status; full tests pipeline is in application_submit.

        # 4. Save draft
        draft_create = ApplicationDraftCreate(
            search_profile_id=search_profile_id,
            resume_id=str(resume_id) if resume_id else "",
            vacancy_id=int(vacancy_id) if vacancy_id else 0,
            employer_id=int(employer_id) if employer_id else None,
            status=status,
            relevance_score=relevance_score,
            relevance_reason=relevance_reason,
            analysis_json=analysis_json,
            full_vacancy_json=vacancy,
            cover_letter=cover_letter,
            cover_letter_status=cover_letter_status,
            has_test=has_test,
            test_status=test_status,
        )
        saved_draft = self._draft_repo.save(draft_create)

        # 5. Save generated cover letter as a separate record
        if cover_letter and saved_draft.id:
            try:
                self._cover_letter_repo.save(
                    CoverLetterCreate(
                        draft_id=saved_draft.id,
                        content=cover_letter,
                        status=cover_letter_status or "generated",
                        template_used=None,
                        ai_generated=False,
                        placeholders=placeholders or {},
                    )
                )
            except Exception as ex:
                logger.warning(
                    "Не удалось сохранить cover_letter для draft %s: %s",
                    saved_draft.id,
                    ex,
                )

        # Persist relevance analysis
        if relevance_result is not None and saved_draft.id:
            self._relevance_repo.save_analysis(saved_draft.id, relevance_result)

        return saved_draft

    # Implementation of ApplicationPort

    def get_draft(self, draft_id: str) -> ApplicationDraft | None:
        """Get application draft by ID."""
        return self._draft_repo.get_by_id(draft_id)

    def get_draft_by_resume_vacancy(
        self, resume_id: str, vacancy_id: int
    ) -> ApplicationDraft | None:
        """Get application draft by resume + vacancy."""
        return self._draft_repo.get_by_resume_vacancy(resume_id, vacancy_id)

    def list_drafts(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ApplicationDraft]:
        """List application drafts with optional status filter."""
        return self._draft_repo.list_all(
            status=status, limit=limit, offset=offset
        )

    def save_draft(self, draft: ApplicationDraftCreate) -> ApplicationDraft:
        """Save an application draft."""
        return self._draft_repo.save(draft)

    def update_draft(self, draft: ApplicationDraft) -> ApplicationDraft:
        """Update an existing application draft."""
        return self._draft_repo.update(draft)

    def delete_draft(self, draft_id: str) -> bool:
        """Delete an application draft."""
        # Cascade: also delete cover_letter and relevance_analysis
        self._cover_letter_repo.delete_by_draft_id(draft_id)
        self._relevance_repo.delete_by_draft_id(draft_id)
        return self._draft_repo.delete(draft_id)


def _analysis_to_dict(result: Any) -> dict:
    """Convert RelevanceResult to dict for analysis_json.

    Doesn't import RelevanceResult directly to avoid circular dependencies
    and accept any duck-typed object (dataclass / NamedTuple).
    """
    out: dict = {"suitable": bool(getattr(result, "suitable", False))}
    score = getattr(result, "score", None)
    if score is not None:
        out["score"] = score
    reason = getattr(result, "reason", None)
    if reason is not None:
        out["reason"] = reason
    raw = getattr(result, "raw_response", None)
    if raw is not None:
        out["raw_response"] = raw
    return out
