"""Legacy :mod:`hh_applicant_tool.utils` package — DEPRECATED (issue #93).

The cross-cutting helpers (text, datetime, logging, JSON) have moved
to :mod:`job_bot.shared.utils`. This package is preserved as a
deprecation shim that re-exports the public API from the VSA
location so legacy call sites (``from hh_applicant_tool.utils import
…``) continue to work for the duration of the VSA migration.

The :class:`Config` shim was removed in issue #142 (Phase D shim
removal). New code should depend on :class:`job_bot.config_auth` (the
VSA :class:`ConfigAuthSlice` and friends) instead. The
``get_config_path`` platform helper is still re-exported here because
:mod:`hh_applicant_tool.constants` uses it to resolve the default
config directory; it is not part of the legacy ``Config`` shim and
is a pure stdlib helper.

Modules that have not moved (``cookiejar``, ``mixins``, ``resume_md``,
``terminal``) remain in place because no VSA slice depends on them yet.
"""

from __future__ import annotations

import warnings

from ._config_path import get_config_path
from .terminal import setup_terminal

warnings.warn(
    "hh_applicant_tool.utils is deprecated, use job_bot.shared.utils instead",
    DeprecationWarning,
    stacklevel=2,
)

# Add all public symbols to __all__ for consistent import behavior
__all__ = [
    "get_config_path",
    "setup_terminal",
]
