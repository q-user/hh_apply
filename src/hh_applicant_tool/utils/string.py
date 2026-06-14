"""Legacy :mod:`hh_applicant_tool.utils.string` shim — DEPRECATED (issue #93).

The implementation now lives in :mod:`job_bot.shared.utils.text`.
This module re-exports the public API and emits a
:class:`DeprecationWarning` on import so legacy call sites remain
greppable. New code should depend on the VSA location directly.
"""

from __future__ import annotations

import warnings

from job_bot.shared.utils.text import (
    bool2str,
    br2nl,
    list2str,
    rand_text,
    shorten,
    strip_tags,
    unescape_string,
)

warnings.warn(
    "hh_applicant_tool.utils.string is deprecated, "
    "use job_bot.shared.utils.text instead",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "bool2str",
    "br2nl",
    "list2str",
    "rand_text",
    "shorten",
    "strip_tags",
    "unescape_string",
]
