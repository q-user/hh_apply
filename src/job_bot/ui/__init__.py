"""``job_bot.ui`` slice — pywebview ``js_api`` bridge (VSA — Issue #150).

The slice replaces the 673-LOC legacy ``hh_applicant_tool.ui.api.Api``
with a slimmed-down ``Api`` class that dispatches into a
:class:`UiApiContext` dataclass.  The public method surface is
byte-for-byte identical to the legacy class, so the existing webview
HTML/JS keeps working unchanged.

Public surface
--------------

* :class:`UiSlice` — the slice's composition root.
* :class:`Api` — the pywebview ``js_api`` bridge.
* :class:`UiApiContext` — the dataclass bundling the slice's
  dependencies.
* :class:`PresetsManager` — named- and last-used-preset persistence.
"""

from .api import Api
from .ports import (
    ConfigPort,
    HhApiClientPort,
    LegacyUseCasePort,
    StoragePort,
    UiApiContext,
)
from .presets import PresetsManager
from .slice import UiSlice

__all__ = [
    "Api",
    "ConfigPort",
    "HhApiClientPort",
    "LegacyUseCasePort",
    "PresetsManager",
    "StoragePort",
    "UiApiContext",
    "UiSlice",
]
