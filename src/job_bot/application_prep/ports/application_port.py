"""Port for application draft operations - used by other slices."""

from __future__ import annotations

from typing import Any, Protocol

from job_bot.application_prep.models.application import (
    ApplicationDraft,
    ApplicationDraftCreate,
)


class ApplicationPort(Protocol):
    """Port for application draft operations."""

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

        Orchestrates:
        1. AI filtering (relevance)
        2. Cover letter generation
        3. (Optional) Test answers
        4. Save ApplicationDraft

        Returns:
            ApplicationDraft with status "prepared" or "rejected",
            or None if vacancy is not interesting.
        """
        ...

    def get_draft(self, draft_id: str) -> ApplicationDraft | None:
        """Get application draft by ID."""
        ...

    def get_draft_by_resume_vacancy(
        self, resume_id: str, vacancy_id: int
    ) -> ApplicationDraft | None:
        """Get application draft by resume + vacancy."""
        ...

    def list_drafts(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ApplicationDraft]:
        """List application drafts with optional status filter."""
        ...

    def save_draft(self, draft: ApplicationDraftCreate) -> ApplicationDraft:
        """Save an application draft."""
        ...

    def update_draft(self, draft: ApplicationDraft) -> ApplicationDraft:
        """Update an existing application draft."""
        ...

    def delete_draft(self, draft_id: str) -> bool:
        """Delete an application draft."""
        ...
