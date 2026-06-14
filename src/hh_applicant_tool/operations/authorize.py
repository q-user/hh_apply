"""Legacy ``authorize`` operation — DEPRECATED as of the #59 VSA switchover.

The browser-driven OAuth login flow now lives in
:mod:`job_bot.config_auth.handlers.auth_browser_login` (the VSA
``config_auth`` slice). This module is preserved as a deprecation
shim that re-exports :class:`Operation` so the CLI parser (which
iterates every ``operations/`` module to build the sub-parser list)
keeps registering the ``authorize`` sub-command unchanged.

New code should depend on the VSA slice directly::

    from job_bot.config_auth.handlers.auth_browser_login import Operation
"""

from __future__ import annotations

import warnings

# Deprecation contract (issue #92): canonical format, ``stacklevel=2``,
# module-level emission so the warning fires the first time the legacy
# path is imported (and therefore the first time the CLI parser walks
# the operations package).
warnings.warn(
    "hh_applicant_tool.operations.authorize is deprecated; use "
    "job_bot.config_auth instead (issue #59).",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export the public surface of the VSA module so legacy callers
# (``from hh_applicant_tool.operations.authorize import Operation``,
# ``... import AuthOp``) keep working without any code change.
from job_bot.config_auth.handlers.auth_browser_login import (  # noqa: E402
    DEFAULT_PROFILE_ID,
    AuthHandler,
    HH_ANDROID_SCHEME,
    Operation,
)

__all__ = [
    "AuthHandler",
    "DEFAULT_PROFILE_ID",
    "HH_ANDROID_SCHEME",
    "Operation",
]
