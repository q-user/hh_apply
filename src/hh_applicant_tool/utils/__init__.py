"""Legacy :mod:`hh_applicant_tool.utils` package — DEPRECATED (issue #93).

The cross-cutting helpers (text, datetime, logging, JSON) have moved
to :mod:`job_bot.shared.utils`. This package is preserved as a
deprecation shim that re-exports the public API from the VSA
location so legacy call sites (``from hh_applicant_tool.utils import
…``) continue to work for the duration of the VSA migration.

Modules that have not moved (``config``, ``cookiejar``, ``mixins``,
``resume_md``, ``terminal``) remain in place because no VSA slice
depends on them yet.
"""

from __future__ import annotations

import warnings

from . import json
from .config import Config, get_config_path
from .date import (
    DATETIME_FORMAT,
    parse_api_datetime,
    try_parse_datetime,
)
from .string import bool2str, list2str, rand_text, shorten
from .terminal import setup_terminal

warnings.warn(
    "hh_applicant_tool.utils is deprecated, use job_bot.shared.utils instead",
    DeprecationWarning,
    stacklevel=2,
)

# Add all public symbols to __all__ for consistent import behavior
__all__ = [
    "Config",
    "get_config_path",
    "DATETIME_FORMAT",
    "parse_api_datetime",
    "try_parse_datetime",
    "shorten",
    "rand_text",
    "bool2str",
    "list2str",
    "setup_terminal",
    "json",
]
