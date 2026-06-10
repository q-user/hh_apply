"""Port for cover letter operations - used by other slices."""

from __future__ import annotations

from typing import Any, Protocol

from job_bot.application_prep.models.cover_letter import (
    CoverLetter,
    CoverLetterCreate,
)


class CoverLetterPort(Protocol):
    """Port for cover letter operations."""

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

        Args:
            vacancy: Vacancy data from HH API
            placeholders: Template placeholders (first_name, last_name, resume_title, vacancy_name, etc.)
            resume_analysis: Text analysis of resume (for AI generation)
            resume: Full resume data (for AI generation)
            force: Force generation even if not required
            required_by_vacancy: Whether vacancy requires a cover letter

        Returns:
            Generated cover letter text (empty string if not required and not forced)
        """
        ...

    def get_cover_letter(self, draft_id: str) -> CoverLetter | None:
        """Get cover letter by draft ID."""
        ...

    def save_cover_letter(self, cover_letter: CoverLetterCreate) -> CoverLetter:
        """Save a cover letter."""
        ...

    def delete_cover_letter(self, draft_id: str) -> bool:
        """Delete cover letter by draft ID."""
        ...
