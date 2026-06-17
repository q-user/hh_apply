"""SkipPort -- interface for the vacancy skip policy.

Implemented by :class:`job_bot.application_submit.handlers.skip_handler.SkipHandler`.
The handler wraps the legacy ``_check_vacancy_skips`` helper extracted
from ``ApplyToVacanciesUseCase`` (issue #145).
"""

from __future__ import annotations

from typing import Any, Protocol


class SkipPort(Protocol):
    """Vacancy skip policy (relations / archived / tests / redirects /
    excluded-filter / AI rejection / blacklist).

    The handler is the in-slice VSA wrapper around the legacy
    ``_check_vacancy_skips`` / ``_save_skipped_vacancy`` /
    ``_is_vacancy_already_skipped`` helpers.
    """

    def check(
        self,
        vacancy: dict[str, Any],
        resume: dict[str, Any],
        do_apply: bool,
        command: Any,
        relevance_handler: Any,
        vacancy_filter_ai: Any,
    ) -> str | None:
        """Check if a vacancy should be skipped.

        Returns a reason string (``"limit_reached"``, ``"already_responded"``,
        ``"archived"``, ``"has_test"``, ``"redirected"``, ``"excluded"``,
        ``"ai_already_skipped"``, ``"ai_rejected"``) or ``None`` if the
        vacancy should be processed.
        """
        ...

    def is_already_skipped(
        self, vacancy: dict[str, Any], resume_id: str | None = None
    ) -> bool:
        """Return True if this vacancy has been previously skipped."""
        ...

    def save_skipped(
        self,
        vacancy: dict[str, Any],
        reason: str,
        resume_id: str | None = None,
    ) -> None:
        """Persist a skipped-vacancy record (legacy or VSA storage)."""
        ...


__all__ = ["SkipPort"]
