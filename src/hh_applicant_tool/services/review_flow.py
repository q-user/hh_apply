"""Legacy :mod:`hh_applicant_tool.services.review_flow` shim — DEPRECATED (issue #87).

The review-flow state machine has moved to the VSA slice:

    job_bot.telegram_bot.services.review_service.ReviewFlowService

This module is kept as a thin re-export shim so existing imports
(``from hh_applicant_tool.services.review_flow import ...``) keep
working. A :class:`DeprecationWarning` is emitted on import. New code
should depend on the VSA location directly.

The legacy module is planned for removal in a future major version.
"""

from __future__ import annotations

import warnings

from job_bot.telegram_bot.services.review_service import (
    CB_CONFIRM_SEND,
    CB_CONFIRM_SKIP,
    CB_COVER_CUSTOM,
    CB_COVER_OK,
    CB_COVER_REGEN,
    CB_INTRO_CONTINUE,
    CB_INTRO_OPEN,
    CB_INTRO_SKIP,
    CB_TEST_CHOOSE,
    CB_TEST_CUSTOM,
    CB_TEST_OK,
    CB_TEST_REGEN,
    STATE_AWAIT_COVER_CUSTOM,
    STATE_AWAIT_COVER_REGEN,
    STATE_AWAIT_TEST_CUSTOM,
    STATE_AWAIT_TEST_REGEN,
    STATE_CONFIRM_APPLY,
    STATE_IDLE,
    STATE_REVIEW_COVER,
    STATE_REVIEW_INTRO,
    STATE_REVIEW_TEST,
    ReviewFlowService,
)
from job_bot.telegram_bot.models.message import InlineButton, OutgoingMessage

warnings.warn(
    "hh_applicant_tool.services.review_flow is deprecated; "
    "use job_bot.telegram_bot.services.review_service instead (issue #87).",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = (
    "InlineButton",
    "OutgoingMessage",
    "ReviewFlowService",
    "CB_INTRO_CONTINUE",
    "CB_INTRO_SKIP",
    "CB_INTRO_OPEN",
    "CB_TEST_OK",
    "CB_TEST_CHOOSE",
    "CB_TEST_REGEN",
    "CB_TEST_CUSTOM",
    "CB_COVER_OK",
    "CB_COVER_REGEN",
    "CB_COVER_CUSTOM",
    "CB_CONFIRM_SEND",
    "CB_CONFIRM_SKIP",
    "STATE_IDLE",
    "STATE_REVIEW_INTRO",
    "STATE_REVIEW_TEST",
    "STATE_AWAIT_TEST_REGEN",
    "STATE_AWAIT_TEST_CUSTOM",
    "STATE_REVIEW_COVER",
    "STATE_AWAIT_COVER_REGEN",
    "STATE_AWAIT_COVER_CUSTOM",
    "STATE_CONFIRM_APPLY",
)
