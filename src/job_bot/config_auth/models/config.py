"""Application configuration domain models.

This is the ``config_auth`` slice's view of the configuration. It mirrors
the shared :class:`job_bot.shared.config.settings.Settings` family of
dataclasses, but adds two things on top:

* A ``profiles`` mapping so a single config file can hold several named
  HH.ru profiles (``prod``, ``dev`` ...) and the active one is selected
  via the ``HH_PROFILE_ID`` environment variable.
* A pluggable ``validate(strict=...)`` step that surfaces missing
  ``client_id`` / ``client_secret`` early in production code paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


@dataclass
class HHConfig:
    """HeadHunter.ru OAuth / API config."""

    client_id: str | None = None
    client_secret: str | None = None
    user_agent: str | None = None
    api_delay: float = 0.345
    redirect_uri: str | None = None
    scope: str | None = None
    base_url: str = "https://api.hh.ru"

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "user_agent": self.user_agent,
            "api_delay": self.api_delay,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "base_url": self.base_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HHConfig:
        return cls(
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            user_agent=data.get("user_agent"),
            api_delay=float(data.get("api_delay", 0.345) or 0.345),
            redirect_uri=data.get("redirect_uri"),
            scope=data.get("scope"),
            base_url=data.get("base_url", "https://api.hh.ru"),
        )


@dataclass
class TelegramConfig:
    """Telegram bot config."""

    bot_token: str | None = None
    allowed_user_ids: list[int] = field(default_factory=list)
    digest_chat_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot_token": self.bot_token,
            "allowed_user_ids": list(self.allowed_user_ids),
            "digest_chat_id": self.digest_chat_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelegramConfig:
        raw_ids = data.get("allowed_user_ids", []) or []
        return cls(
            bot_token=data.get("bot_token"),
            allowed_user_ids=[int(x) for x in raw_ids],
            digest_chat_id=(
                int(data["digest_chat_id"])
                if data.get("digest_chat_id") is not None
                else None
            ),
        )


@dataclass
class AIClientConfig:
    """AI provider config."""

    api_key: str | None = None
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    timeout: float = 60.0
    max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AIClientConfig:
        return cls(
            api_key=data.get("api_key"),
            base_url=data.get("base_url"),
            model=data.get("model", "gpt-4o-mini"),
            timeout=float(data.get("timeout", 60.0) or 60.0),
            max_retries=int(data.get("max_retries", 3) or 3),
        )


@dataclass
class MaxConfig:
    """MAX messenger config."""

    bot_token: str | None = None
    api_url: str = "https://botapi.max.ru"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot_token": self.bot_token,
            "api_url": self.api_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaxConfig:
        return cls(
            bot_token=data.get("bot_token"),
            api_url=data.get("api_url", "https://botapi.max.ru"),
        )


@dataclass
class SMTPConfig:
    """SMTP email config."""

    host: str | None = None
    port: int = 587
    username: str | None = None
    password: str | None = None
    from_email: str | None = None
    use_tls: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "from_email": self.from_email,
            "use_tls": self.use_tls,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SMTPConfig:
        return cls(
            host=data.get("host"),
            port=int(data.get("port", 587) or 587),
            username=data.get("username"),
            password=data.get("password"),
            from_email=data.get("from_email"),
            use_tls=bool(data.get("use_tls", True)),
        )


# ---------------------------------------------------------------------------
# Top-level AppConfig
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    """Top-level application config.

    ``profiles`` is a mapping ``name -> HHConfig`` that lets a single
    config file hold several named HH.ru profiles. ``active_profile`` is
    the name of the currently selected profile, or ``None`` to use the
    top-level ``hh`` config.
    """

    hh: HHConfig = field(default_factory=HHConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    ai: AIClientConfig = field(default_factory=AIClientConfig)
    max: MaxConfig = field(default_factory=MaxConfig)
    smtp: SMTPConfig = field(default_factory=SMTPConfig)
    profiles: dict[str, HHConfig] = field(default_factory=dict)
    active_profile: str | None = None

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    def add_profile(self, name: str, hh: HHConfig) -> None:
        """Register a new named HH profile (overwriting any existing one)."""
        self.profiles[name] = hh

    def remove_profile(self, name: str) -> None:
        """Remove a named profile. No-op if it doesn't exist."""
        self.profiles.pop(name, None)
        if self.active_profile == name:
            self.active_profile = None

    def list_profiles(self) -> list[str]:
        """Return the list of profile names."""
        return list(self.profiles.keys())

    def get_profile(self, name: str) -> HHConfig | None:
        """Return the named profile, or ``None``."""
        return self.profiles.get(name)

    def get_active_hh_config(self) -> HHConfig:
        """Return the active HH config.

        * If ``active_profile`` is set and exists, return it.
        * Otherwise, return the top-level ``hh`` config.
        """
        if self.active_profile and self.active_profile in self.profiles:
            return self.profiles[self.active_profile]
        return self.hh

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "hh": self.hh.to_dict(),
            "telegram": self.telegram.to_dict(),
            "ai": self.ai.to_dict(),
            "max": self.max.to_dict(),
            "smtp": self.smtp.to_dict(),
            "profiles": {
                name: cfg.to_dict() for name, cfg in self.profiles.items()
            },
            "active_profile": self.active_profile,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        profiles_raw = data.get("profiles", {}) or {}
        profiles = {
            name: HHConfig.from_dict(p) for name, p in profiles_raw.items()
        }
        return cls(
            hh=HHConfig.from_dict(data.get("hh", {}) or {}),
            telegram=TelegramConfig.from_dict(data.get("telegram", {}) or {}),
            ai=AIClientConfig.from_dict(data.get("ai", {}) or {}),
            max=MaxConfig.from_dict(data.get("max", {}) or {}),
            smtp=SMTPConfig.from_dict(data.get("smtp", {}) or {}),
            profiles=profiles,
            active_profile=data.get("active_profile"),
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, strict: bool = False) -> None:
        """Validate the config.

        In ``strict=True`` mode (production), this raises :class:`ValueError`
        if the top-level HH config is missing ``client_id`` or
        ``client_secret`` *and* no profiles are configured. This guards
        against running the bot with an unconfigured HH client.
        """
        if not strict:
            return
        active = self.get_active_hh_config()
        if not active.client_id or not active.client_secret:
            raise ValueError(
                "HH config is missing client_id and/or client_secret. "
                "Configure them in the config file or via a profile."
            )
