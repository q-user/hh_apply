"""RetryHandler -- backoff schedule and give-up policy for the worker.

Values are kept in sync with the legacy ``apply_worker`` module so the
slice's behaviour matches the rest of the project.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Backoff schedule (seconds) by attempt number (1-indexed):
# 1 -> 5 min, 2 -> 15 min, 3+ -> 1 h. The last value is repeated for
# any attempt past the schedule length.
_BACKOFF_SECONDS: tuple[int, ...] = (
    5 * 60,  # attempt 1
    15 * 60,  # attempt 2
    60 * 60,  # attempt 3
    60 * 60,  # attempt 4
)

DEFAULT_MAX_ATTEMPTS = 5


class RetryHandler:
    """Backoff / max-attempts policy for the apply-worker."""

    @staticmethod
    def backoff_seconds(attempt: int) -> int:
        """Return the backoff (seconds) for the given attempt number.

        ``attempt < 1`` returns 0. Attempts past the end of the
        schedule are clipped to the last entry (60 min).
        """
        if attempt < 1:
            return 0
        return _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]

    @staticmethod
    def should_retry(attempt: int, max_attempts: int) -> bool:
        """Return ``True`` if the worker should schedule another attempt."""
        return attempt < max_attempts

    @staticmethod
    def next_attempt_at(attempt: int, now: datetime) -> str:
        """Compute the ``next_attempt_at`` ISO timestamp for ``attempt``."""
        delay = RetryHandler.backoff_seconds(attempt)
        return (now + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")


__all__ = [
    "RetryHandler",
    "DEFAULT_MAX_ATTEMPTS",
]
