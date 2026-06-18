"""RetryPolicyHandler -- exception classification for the per-vacancy apply step (issue #201).

In-slice VSA wrapper for the per-vacancy ``try/except`` block that used
to live inside the slice's :meth:`_apply_to_resume`. Decides whether to
continue or break the loop based on the raised exception type:

* :class:`LimitExceeded` -> :attr:`RetryAction.BREAK`, set
  ``limit_reached=True`` and ``do_apply=False``.
* :class:`ApiError` -> :attr:`RetryAction.CONTINUE`, logged at WARNING.
* :class:`BadResponse`, :class:`AIError` -> :attr:`RetryAction.CONTINUE`,
  logged at ERROR.
* Any other exception -> :attr:`RetryAction.CONTINUE`, logged at ERROR.

Issue #201: extracted from :class:`ApplicationSubmitSlice._apply_to_resume`.
The apply loop calls :meth:`classify` (or :meth:`run`) instead of doing
the ``isinstance`` chain inline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from job_bot.application_submit.errors import LimitExceeded
from job_bot.shared.ai._errors import AIError
from job_bot.shared.api.errors import ApiError, BadResponse

logger = logging.getLogger(__package__)


class RetryAction(str, Enum):
    """What the apply loop should do after a per-vacancy step."""

    CONTINUE = "continue"
    BREAK = "break"


@dataclass(frozen=True)
class RetryDecision:
    """The retry-policy decision for a single per-vacancy step.

    Attributes:
        action: ``CONTINUE`` (next vacancy) or ``BREAK`` (stop the loop).
        limit_reached: ``True`` iff the HH API told us the daily limit
            was hit; the caller should propagate this to the outer
            pipeline result.
        do_apply: ``False`` iff the loop should switch to "skip" mode
            for the rest of the resumes (e.g. on a per-session limit).
            Mirrors the legacy ``do_apply`` flag.
    """

    action: RetryAction
    limit_reached: bool = False
    do_apply: bool = True


class RetryPolicyHandler:
    """In-slice retry-policy handler (issue #201).

    Encapsulates the per-vacancy exception classification policy used
    by :meth:`ApplicationSubmitSlice._apply_to_resume`. The handler is
    stateless; one instance can be shared across the slice and across
    resumes.
    """

    # ─── Pure classification ────────────────────────────────────

    def classify(self, ex: BaseException) -> RetryDecision:
        """Map ``ex`` to a :class:`RetryDecision`.

        Pure function: same exception always returns the same decision.
        Does *not* log; the caller (:meth:`run`) handles logging.
        """
        if isinstance(ex, LimitExceeded):
            return RetryDecision(
                action=RetryAction.BREAK,
                limit_reached=True,
                do_apply=False,
            )
        if isinstance(ex, ApiError):
            return RetryDecision(
                action=RetryAction.CONTINUE,
                limit_reached=False,
                do_apply=True,
            )
        if isinstance(ex, (BadResponse, AIError)):
            return RetryDecision(
                action=RetryAction.CONTINUE,
                limit_reached=False,
                do_apply=True,
            )
        return RetryDecision(
            action=RetryAction.CONTINUE,
            limit_reached=False,
            do_apply=True,
        )

    # ─── Run + classify + log ───────────────────────────────────

    def run(
        self,
        action: Callable[[], Any],
        *,
        applied_count: int | None = None,
    ) -> RetryDecision:
        """Run ``action`` and classify the exception (if any).

        Mirrors the legacy inline ``try/except`` behaviour: limit
        errors break the loop, ``ApiError`` is logged at WARNING,
        other errors are logged at ERROR. The returned
        :class:`RetryDecision` tells the loop whether to continue
        or break.

        Args:
            action: zero-arg callable that performs the per-vacancy
                apply step. May raise any of the known / unknown
                exceptions handled by :meth:`classify`.
            applied_count: current applied-count for logging on
                :class:`LimitExceeded`. Optional.

        Returns:
            :class:`RetryDecision` describing what the apply loop
            should do next.
        """
        try:
            action()
        except Exception as ex:  # noqa: BLE001  # classify() maps every exception type to a decision
            decision = self.classify(ex)
            if decision.action == RetryAction.BREAK:
                logger.warning(
                    "Достигли лимита на отклики (отправлено в этой сессии: %s)",
                    applied_count,
                )
            elif isinstance(ex, ApiError):
                logger.warning(ex)
            else:
                logger.error("%s: %s", type(ex).__name__, ex)
                if isinstance(ex, (BadResponse, AIError)):
                    logger.error(ex)
            return decision
        return RetryDecision(
            action=RetryAction.CONTINUE,
            limit_reached=False,
            do_apply=True,
        )


__all__ = ["RetryAction", "RetryDecision", "RetryPolicyHandler"]
