"""Legacy :mod:`hh_applicant_tool.utils.log` shim — DEPRECATED (issue #93).

The implementation now lives in :mod:`job_bot.shared.utils.logging`.
This module re-exports the public API and emits a
:class:`DeprecationWarning` on import so legacy call sites remain
greppable. New code should depend on the VSA location directly.
"""

from __future__ import annotations

import warnings

from job_bot.shared.utils.logging import (
    MAX_LOG_SIZE,
    Color,
    ColorHandler,
    RedactingFilter,
    setup_logger,
)

warnings.warn(
    "hh_applicant_tool.utils.log is deprecated, "
    "use job_bot.shared.utils.logging instead",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "Color",
    "ColorHandler",
    "MAX_LOG_SIZE",
    "RedactingFilter",
    "setup_logger",
]
