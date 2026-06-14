"""Legacy :mod:`hh_applicant_tool.utils.date` shim — DEPRECATED (issue #93).

The implementation now lives in
:mod:`job_bot.shared.utils.datetime_utils`. This module re-exports the
public API and emits a :class:`DeprecationWarning` on import so
legacy call sites remain greppable. New code should depend on the
VSA location directly.
"""

from __future__ import annotations

import warnings

from job_bot.shared.utils.datetime_utils import (
    DATETIME_FORMAT,
    parse_api_datetime,
    try_parse_datetime,
)

warnings.warn(
    "hh_applicant_tool.utils.date is deprecated, "
    "use job_bot.shared.utils.datetime_utils instead",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "DATETIME_FORMAT",
    "parse_api_datetime",
    "try_parse_datetime",
]
