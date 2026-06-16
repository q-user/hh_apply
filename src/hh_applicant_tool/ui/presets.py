"""Legacy shim for ``hh_applicant_tool.ui.presets`` (VSA — Issue #150).

The :class:`PresetsManager` was moved wholesale to
:mod:`job_bot.ui.presets` as part of the VSA migration.  This module
keeps the **old import path working** so the existing test suite
(``tests/test_ui_presets.py`` — 28 tests) and any external caller
keeps compiling.

The module is implemented as a :pep:`562` lazy re-export so a plain
``import hh_applicant_tool.ui.presets`` does **not** emit a
deprecation warning; the canonical ``use job_bot.ui.presets instead``
warning fires only on attribute access (e.g.
``from hh_applicant_tool.ui.presets import PresetsManager``).
"""

from __future__ import annotations

import importlib
import warnings
from typing import Any

_DEPRECATION_MESSAGE = (
    "hh_applicant_tool.ui.presets is deprecated; "
    "use job_bot.ui.presets instead (issue #150)."
)


# Symbol name -> ``module.attr`` reference for the
# ``from X import Y`` access pattern.  Preserves the public surface
# of the legacy module for one release window.
_RAW_SYMBOLS: dict[str, str] = {
    "LAST_USED_KEY": "job_bot.ui.presets.LAST_USED_KEY",
    "MAX_NAME_LEN": "job_bot.ui.presets.MAX_NAME_LEN",
    "MAX_PARAMS_BYTES": "job_bot.ui.presets.MAX_PARAMS_BYTES",
    "PRESET_PREFIX": "job_bot.ui.presets.PRESET_PREFIX",
    "PresetValidationError": "job_bot.ui.presets.PresetValidationError",
    "PresetsManager": "job_bot.ui.presets.PresetsManager",
}


def __getattr__(name: str) -> Any:  # PEP 562
    """Lazy re-export hook: fire the deprecation warning on attribute access."""
    if name in _RAW_SYMBOLS:
        mod_name, _, attr_name = _RAW_SYMBOLS[name].rpartition(".")
        mod = importlib.import_module(mod_name)
        warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
        return getattr(mod, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_RAW_SYMBOLS)
