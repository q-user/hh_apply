"""Legacy :mod:`hh_applicant_tool.utils.json` shim — DEPRECATED (issue #93).

The implementation now lives in :mod:`job_bot.shared.utils.json_utils`.
This module re-exports the public API and emits a
:class:`DeprecationWarning` on import so legacy call sites remain
greppable. New code should depend on the VSA location directly.
"""

from __future__ import annotations

import warnings

from job_bot.shared.utils.json_utils import (
    JSONDecoder,
    JSONEncoder,
    dump,
    dumps,
    load,
    loads,
)

warnings.warn(
    "hh_applicant_tool.utils.json is deprecated, "
    "use job_bot.shared.utils.json_utils instead",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["JSONEncoder", "JSONDecoder", "dump", "dumps", "load", "loads"]
