"""RetryPolicyPort -- interface for per-vacancy exception classification.

Implemented by :class:`job_bot.application_submit.handlers.retry_policy_handler.RetryPolicyHandler`.
The handler wraps the per-vacancy ``try/except`` block that used to live
inline in :meth:`ApplicationSubmitSlice._apply_to_resume` (issue #201).
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from job_bot.application_submit.handlers.retry_policy_handler import (
    RetryDecision,
)


class RetryPolicyPort(Protocol):
    """Exception classification policy for the per-vacancy apply step."""

    def classify(self, ex: BaseException) -> RetryDecision:
        """Map ``ex`` to a :class:`RetryDecision`.

        Pure function: same exception always returns the same decision.
        Does *not* log; :meth:`run` handles logging.
        """
        ...

    def run(
        self,
        action: Callable[[], Any],
        *,
        applied_count: int | None = None,
    ) -> RetryDecision:
        """Run ``action`` and classify the raised exception (if any).

        Logs the same messages the legacy inline ``try/except`` block
        logged; the returned :class:`RetryDecision` tells the loop
        whether to continue or break.
        """
        ...


__all__ = ["RetryPolicyPort"]
