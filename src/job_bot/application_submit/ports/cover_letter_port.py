"""CoverLetterPort -- interface for cover-letter generation in the submit phase.

The submit-phase handler adapts
:class:`job_bot.application_prep.handlers.cover_letter_handler.CoverLetterHandler`
to the submit pipeline (the prep and submit phases share the same
underlying handler, but the submit-phase wrapper is kept thin so the
slice can reason about each phase independently -- issue #145).
"""

from __future__ import annotations

from typing import Any, Protocol


class CoverLetterPort(Protocol):
    """Cover-letter generation in the submit phase."""

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
        """Generate a cover letter.

        Returns the generated text (empty string if not required and
        not forced).
        """
        ...


__all__ = ["CoverLetterPort"]
