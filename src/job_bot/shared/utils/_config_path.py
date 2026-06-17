"""Cross-platform config directory helper (issue #151).

VSA port of the legacy ``hh_applicant_tool.utils._config_path`` module.
Resolves the default per-user config directory for Windows, macOS and
Linux/BSD using the same XDG / APPDATA conventions the legacy helper
used. New code should depend on the VSA :class:`job_bot.config_auth`
slice for config; the legacy :class:`hh_applicant_tool.constants`
module imports :func:`get_config_path` from here directly.
"""

from __future__ import annotations

import platform
from functools import cache
from os import getenv
from pathlib import Path

__all__ = ["get_config_path"]


@cache
def get_config_path() -> Path:
    """Return the platform default per-user config directory.

    * **Windows**: ``%APPDATA%`` (falls back to ``~/AppData/Roaming``).
    * **macOS**: ``~/Library/Application Support``.
    * **Other** (Linux/BSD): ``$XDG_CONFIG_HOME`` (falls back to
      ``~/.config``).
    """
    match platform.system():
        case "Windows":
            return Path(getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        case "Darwin":
            return Path.home() / "Library" / "Application Support"
        case _:
            return Path(getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
