"""Cross-platform ``get_config_path`` helper.

Issue #142: the legacy :class:`Config` shim was removed, but the
platform-aware ``get_config_path()`` helper is still used by
:mod:`hh_applicant_tool.constants` to resolve the default config
directory. New code should depend on the VSA
:class:`job_bot.config_auth` slice for config; the legacy
``Config`` JSON file is read directly by the CLI runtime.
"""

from __future__ import annotations

import platform
from functools import cache
from os import getenv
from pathlib import Path


@cache
def get_config_path() -> Path:
    match platform.system():
        case "Windows":
            return Path(getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        case "Darwin":
            return Path.home() / "Library" / "Application Support"
        case _:
            return Path(getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
