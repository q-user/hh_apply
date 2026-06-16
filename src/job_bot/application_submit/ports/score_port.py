"""ScorePort -- interface for AI relevance filtering.

Implemented by :class:`job_bot.application_submit.handlers.score_handler.ScoreHandler`.
The handler delegates the actual AI calls to
:class:`job_bot.application_prep.handlers.relevance_handler.RelevanceHandler`.
"""

from __future__ import annotations

from typing import Any, Protocol


class ScorePort(Protocol):
    """AI relevance filtering (per-resume init + per-vacancy suitability)."""

    def init_ai_filter(self, resume: dict[str, Any], command: Any) -> str:
        """Initialize the AI filter for a resume.

        Returns the ``resume_analysis`` text (used downstream by the
        cover-letter handler). Idempotent: caches per-resume results
        on the underlying ``RelevanceHandler``.
        """
        ...

    def is_suitable(self, vacancy: dict[str, Any], command: Any) -> bool:
        """Check if a vacancy is suitable per the AI filter.

        When ``command.ai_filter`` is ``None``, returns ``True`` (no
        filtering requested). Otherwise delegates to the heavy or
        light ``is_suitable_*`` method on the underlying
        ``RelevanceHandler``.
        """
        ...


__all__ = ["ScorePort"]
