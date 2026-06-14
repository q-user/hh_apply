"""Legacy ``Config`` class — DEPRECATED as of the #59 VSA switchover.

The CLI runtime no longer instantiates :class:`Config`; the
:attr:`HHApplicantTool.config` property now returns the VSA
``_ConfigAdapter`` (built on top of
:class:`job_bot.config_auth.slice.ConfigAuthSlice`) instead. This
module is kept for back-compat with any external code or test that
imports :class:`Config` directly -- importing the module raises a
runtime :class:`DeprecationWarning` so the call site is greppable.

New code should depend on the VSA slice directly::

    from job_bot.config_auth import (
        AppConfig,
        AuthHandler,
        ConfigAuthSlice,
        create_config_auth_slice,
    )

or read config through ``HHApplicantTool().config``, which is now
backed by the VSA slice.
"""

from __future__ import annotations

import platform
import warnings
from functools import cache
from os import getenv
from pathlib import Path
from threading import Lock
from typing import Any

from . import json

# Deprecation contract (issue #92): canonical format, ``stacklevel=2``,
# module-level emission so the warning fires the first time the
# legacy path is imported (the test_issue_92_deprecation contract
# relies on this and reloads the module to observe it).
warnings.warn(
    "hh_applicant_tool.utils.config is deprecated; use job_bot.config_auth instead (issue #59).",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export the VSA public API so legacy callers that want to migrate
# can pick up the canonical names from this module without changing
# their import path. The names below mirror ``job_bot.config_auth``'s
# ``__all__``; adding a new symbol to the slice does not require
# touching this shim.
from job_bot.config_auth import (  # noqa: E402
    AIClientConfig,
    AppConfig,
    AuthHandler,
    ConfigAuthSlice,
    ConfigHandler,
    HHConfig,
    MaxConfig,
    OAuthCredentials,
    SMTPConfig,
    TelegramConfig,
    UserHandler,
    UserProfile,
    create_config_auth_slice,
)
from job_bot.config_auth.ports import AuthPort, ConfigPort, UserPort  # noqa: E402

__all__ = [
    # Back-compat: legacy ``Config`` + ``get_config_path`` are kept so
    # existing scripts/tests that imported them from this module
    # keep working. The ``Config`` class is a thin shim that re-reads
    # from a JSON file on disk (matching the legacy behaviour) and is
    # **not** equivalent to the VSA ``_ConfigAdapter`` -- the VSA
    # adapter is what ``HHApplicantTool().config`` returns now.
    "Config",
    "get_config_path",
    # VSA re-exports -- the canonical migration path.
    "AIClientConfig",
    "AppConfig",
    "AuthHandler",
    "AuthPort",
    "ConfigAuthSlice",
    "ConfigHandler",
    "ConfigPort",
    "HHConfig",
    "MaxConfig",
    "OAuthCredentials",
    "SMTPConfig",
    "TelegramConfig",
    "UserHandler",
    "UserPort",
    "UserProfile",
    "create_config_auth_slice",
]


@cache
def get_config_path() -> Path:
    match platform.system():
        case "Windows":
            return Path(getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        case "Darwin":
            return Path.home() / "Library" / "Application Support"
        case _:
            return Path(getenv("XDG_CONFIG_HOME", Path.home() / ".config"))


class Config(dict):
    """Legacy ``Config`` — DEPRECATED, use :class:`ConfigAuthSlice` instead.

    Kept verbatim from the pre-#59 implementation so legacy scripts
    that do ``from hh_applicant_tool.utils.config import Config``
    continue to work. New code should depend on the VSA slice.
    """

    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path or get_config_path())
        self._lock = Lock()
        self.load()

    def load(self) -> None:
        if self._config_path.exists():
            with self._lock:
                with self._config_path.open(
                    "r", encoding="utf-8", errors="replace"
                ) as f:
                    self.update(json.load(f))

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.update(*args, **kwargs)
        self._config_path.parent.mkdir(exist_ok=True, parents=True)
        with self._lock:
            with self._config_path.open(
                "w+", encoding="utf-8", errors="replace"
            ) as fp:
                json.dump(
                    self,
                    fp,
                    indent=2,
                    sort_keys=True,
                )

    __getitem__ = dict.get

    def __repr__(self) -> str:
        return str(self._config_path)
