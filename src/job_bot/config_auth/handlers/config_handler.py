"""Config handler — load and save :class:`AppConfig` to a JSON file.

Two storage shapes are accepted on load (and emitted on save) so the slice
can read both legacy ``hh_applicant_tool`` configs and the new
``job_bot`` format:

Legacy (``config.example.json``-style, flat)::

    {
        "client_id": "...",
        "client_secret": "...",
        "api_delay": 0.345,
        "user_agent": "",
        "token": {
            "access_token": "...",
            "refresh_token": "...",
            "access_expires_at": 0,
        }
    }

New (nested under section names)::

    {
        "hh": {"client_id": "...", "client_secret": "...", ...},
        "telegram": {"bot_token": "..."},
        "ai": {...},
        "max": {...},
        "smtp": {...},
        "profiles": {"prod": {"client_id": "..."}},
        "active_profile": "prod",
    }

Saves are atomic (write to ``<path>.tmp`` then ``os.replace``) and
optionally create a ``.bak`` copy of the previous file.

Issue #206: the ``HH_PROFILE_ID`` env var lookup goes through the
injected :class:`SecretsManager` so a deployment can opt to keep
that key in the OS keyring rather than ``os.environ``. The default
is still :class:`EnvBackend`, so behaviour for users who never set
anything is preserved.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from job_bot.config_auth.models.config import AppConfig, HHConfig
from job_bot.shared.secrets import SecretsManager


class ConfigHandler:
    """Load / save :class:`AppConfig` to a JSON file.

    Args:
        secrets_manager: The :class:`SecretsManager` used to look up
            the ``HH_PROFILE_ID`` env var on load. A fresh
            ``SecretsManager(EnvBackend())`` is used when ``None`` is
            passed, which keeps the behaviour identical to the
            pre-issue-#206 code path.
    """

    def __init__(
        self,
        secrets_manager: SecretsManager | None = None,
    ) -> None:
        self._secrets = secrets_manager or SecretsManager()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: Path | str, strict: bool = False) -> AppConfig:
        """Load an :class:`AppConfig` from ``path``.

        * If the file does not exist, returns a default :class:`AppConfig`.
        * If the file is corrupt (invalid JSON), raises :class:`ValueError`
          when ``strict=True``; otherwise returns a default config.
        * The ``HH_PROFILE_ID`` env var is honoured: when set, the matching
          profile becomes the active one. The lookup goes through
          :class:`SecretsManager` (issue #206) so a deployment may
          keep that key in the OS keyring via
          ``HH_SECRETS_BACKEND=keyring``.
        """
        path = Path(path)
        if not path.exists():
            config = AppConfig()
        else:
            try:
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else {}
            except (OSError, json.JSONDecodeError) as exc:
                if strict:
                    raise ValueError(
                        f"Could not load config from {path}: {exc}"
                    ) from exc
                config = AppConfig()
            else:
                if not isinstance(data, dict):
                    if strict:
                        raise ValueError(
                            f"Config at {path} is not a JSON object"
                        )
                    config = AppConfig()
                else:
                    config = self._dict_to_config(data)

        # Honour HH_PROFILE_ID env var (issue #206: via SecretsManager
        # so the same key can be served from the OS keyring when the
        # operator has opted into ``HH_SECRETS_BACKEND=keyring``).
        env_profile = self._secrets.get("HH_PROFILE_ID")
        if env_profile and env_profile in config.profiles:
            config.active_profile = env_profile
        return config

    def save(
        self,
        config: AppConfig,
        path: Path | str,
        backup: bool = False,
    ) -> None:
        """Save an :class:`AppConfig` to ``path`` atomically.

        The write is performed via a sibling ``.tmp`` file and an
        ``os.replace`` to ensure the target is never left in a
        half-written state. When ``backup=True`` and ``path`` already
        exists, the previous contents are copied to ``<path>.bak``.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if backup and path.exists():
            backup_path = path.with_suffix(path.suffix + ".bak")
            backup_path.write_bytes(path.read_bytes())

        data = config.to_dict()
        serialised = json.dumps(data, ensure_ascii=False, indent=2)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(serialised, encoding="utf-8")
        # Atomic replace — on the same filesystem
        os.replace(tmp_path, path)

    # ------------------------------------------------------------------
    # Legacy / new format conversion
    # ------------------------------------------------------------------

    def _dict_to_config(self, data: dict[str, Any]) -> AppConfig:
        """Convert a raw config dict to :class:`AppConfig`.

        Accepts both the new nested format and the legacy flat format
        used by ``config.example.json``. When both styles are mixed in
        the same file (top-level ``client_id`` plus a ``profiles``
        block), we honour the profiles block.
        """
        new_format_keys = {"hh", "telegram", "ai", "max", "smtp", "profiles"}
        if new_format_keys & set(data.keys()):
            return AppConfig.from_dict(self._merge_legacy(data))
        return self._legacy_to_config(data)

    @staticmethod
    def _merge_legacy(data: dict[str, Any]) -> dict[str, Any]:
        """Merge any top-level legacy keys into the ``hh`` sub-config."""
        merged = dict(data)
        legacy_hh_keys = {
            "client_id": "client_id",
            "client_secret": "client_secret",
            "user_agent": "user_agent",
            "api_delay": "api_delay",
            "redirect_uri": "redirect_uri",
            "scope": "scope",
        }
        if any(k in merged for k in legacy_hh_keys):
            hh_section = dict(merged.get("hh") or {})
            for legacy_key, hh_key in legacy_hh_keys.items():
                if legacy_key in merged and hh_key not in hh_section:
                    hh_section[hh_key] = merged[legacy_key]
                    # Don't pollute the new format's top-level
                    merged.pop(legacy_key, None)
            merged["hh"] = hh_section
        return merged

    @staticmethod
    def _legacy_to_config(data: dict[str, Any]) -> AppConfig:
        """Map a legacy flat config to :class:`AppConfig`."""
        hh = HHConfig(
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            user_agent=data.get("user_agent"),
            api_delay=float(data.get("api_delay", 0.345) or 0.345),
        )
        # Legacy ``token`` blob is *not* part of AppConfig; the
        # ``auth_handler`` is the source of truth for credentials. We just
        # silently drop it here.
        return AppConfig(hh=hh)
