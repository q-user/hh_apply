"""Legacy shim for ``hh_applicant_tool.ui.api`` (VSA — Issue #150).

Drop-in replacement: any caller that does
``from hh_applicant_tool.ui.api import Api`` keeps working — the
shim subclasses the new :class:`job_bot.ui.Api`, builds a
:class:`UiApiContext` from the legacy ``HHApplicantTool``, and
overrides :meth:`set_window` to wire the pywebview window into the
context.

Glue code (context construction, the module-level progress / auth
event sinks) lives in :mod:`hh_applicant_tool.ui._legacy_context`
so the shim itself stays under the 30-LOC budget.
"""

from __future__ import annotations

import warnings
from typing import Any

from job_bot.ui import Api as _NewApi
from job_bot.ui import UiApiContext as _UiApiContext

from . import _legacy_context as _helpers

_DEPRECATION_MESSAGE = (
    "hh_applicant_tool.ui is deprecated; use job_bot.ui instead (issue #150)."
)


class Api(_NewApi):
    """Legacy ``Api(tool)`` shim — drop-in replacement."""

    def __init__(self, tool: Any) -> None:
        ctx: _UiApiContext = _helpers.build_legacy_context(tool)
        super().__init__(ctx)
        self._ctx = ctx
        self._tool = tool
        self._window: Any = None
        _helpers.register_active_shim(self)

    def set_window(self, window: Any) -> None:
        self._window = window
        self._ctx.window = window


# Re-export the legacy ``BadResponse`` for any caller that did
# ``from hh_applicant_tool.ui.api import BadResponse`` — the
# legacy module used to import it at top level.
from ..api.errors import BadResponse  # noqa: E402, F401

__all__ = ["Api", "BadResponse"]

warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=1)
