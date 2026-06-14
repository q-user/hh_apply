"""Deprecated shim. Use :mod:`job_bot.telegram_bot.services.daily_digest_service`.

This module is part of the VSA switchover (issue #54). The implementation
has moved to the VSA ``telegram_bot`` slice; this shim is kept for
backward compatibility and emits a :class:`DeprecationWarning` on
import. The legacy module is planned for removal in a future major
version. New code should depend on the VSA location directly.

VSA target: :mod:`job_bot.telegram_bot.services.daily_digest_service`.
"""

from __future__ import annotations

import warnings

from job_bot.telegram_bot.services.daily_digest_service import (  # noqa: F401
    LAST_DIGEST_KEY,
    DailyDigestService,
    DigestResult,
    DraftGroup,
    __all__ as _VSA_ALL,
)

warnings.warn(
    "hh_applicant_tool.services.daily_digest is deprecated; "
    "use job_bot.telegram_bot.services.daily_digest_service instead (issue #54).",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = (
    "LAST_DIGEST_KEY",
    "DailyDigestService",
    "DigestResult",
    "DraftGroup",
)
