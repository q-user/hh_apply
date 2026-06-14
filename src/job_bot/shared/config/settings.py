"""Shared configuration settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DatabaseSettings:
    """Database configuration."""

    path: Path = Path("data/job_bot.db")


@dataclass
class HHApiSettings:
    """HH.ru API configuration."""

    base_url: str = "https://api.hh.ru"
    user_agent: str = "job_bot/0.1.0"
    timeout: int = 30
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None


@dataclass
class AISettings:
    """AI/OpenAI configuration."""

    api_key: str | None = None
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    timeout: float = 60.0
    max_retries: int = 3


@dataclass
class TelegramSettings:
    """Telegram bot configuration."""

    bot_token: str | None = None
    allowed_user_ids: list[int] = field(default_factory=list)
    digest_chat_id: int | None = None


@dataclass
class MaxSettings:
    """MAX messenger configuration."""

    bot_token: str | None = None
    api_url: str = "https://botapi.max.ru"


@dataclass
class SMTPSettings:
    """SMTP email configuration."""

    host: str | None = None
    port: int = 587
    username: str | None = None
    password: str | None = None
    from_email: str | None = None
    use_tls: bool = True


@dataclass
class Settings:
    """Main application settings."""

    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    hh_api: HHApiSettings = field(default_factory=HHApiSettings)
    ai: AISettings = field(default_factory=AISettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    max: MaxSettings = field(default_factory=MaxSettings)
    smtp: SMTPSettings = field(default_factory=SMTPSettings)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        """Create Settings from a dictionary (e.g., from YAML/JSON config)."""
        settings = cls()

        if "database" in data:
            settings.database = DatabaseSettings(**data["database"])
        if "hh_api" in data:
            settings.hh_api = HHApiSettings(**data["hh_api"])
        if "ai" in data:
            settings.ai = AISettings(**data["ai"])
        if "telegram" in data:
            settings.telegram = TelegramSettings(**data["telegram"])
        if "max" in data:
            settings.max = MaxSettings(**data["max"])
        if "smtp" in data:
            settings.smtp = SMTPSettings(**data["smtp"])

        return settings

    def to_dict(self) -> dict[str, Any]:
        """Convert settings to dictionary."""
        return {
            "database": {"path": str(self.database.path)},
            "hh_api": {
                "base_url": self.hh_api.base_url,
                "user_agent": self.hh_api.user_agent,
                "timeout": self.hh_api.timeout,
                "client_id": self.hh_api.client_id,
                "client_secret": self.hh_api.client_secret,
                "redirect_uri": self.hh_api.redirect_uri,
            },
            "ai": {
                "api_key": self.ai.api_key,
                "base_url": self.ai.base_url,
                "model": self.ai.model,
                "timeout": self.ai.timeout,
                "max_retries": self.ai.max_retries,
            },
            "telegram": {
                "bot_token": self.telegram.bot_token,
                "allowed_user_ids": self.telegram.allowed_user_ids,
                "digest_chat_id": self.telegram.digest_chat_id,
            },
            "max": {
                "bot_token": self.max.bot_token,
                "api_url": self.max.api_url,
            },
            "smtp": {
                "host": self.smtp.host,
                "port": self.smtp.port,
                "username": self.smtp.username,
                "password": self.smtp.password,
                "from_email": self.smtp.from_email,
                "use_tls": self.smtp.use_tls,
            },
        }


def load_settings(config_path: Path | str | None = None) -> Settings:
    """Load settings from config file."""
    import yaml

    if config_path is None:
        # Default config paths
        for path in [
            Path("config.yaml"),
            Path("config.yml"),
            Path.home() / ".config" / "job_bot" / "config.yaml",
            Path("/etc/job_bot/config.yaml"),
        ]:
            if path.exists():
                config_path = path
                break

    if config_path and Path(config_path).exists():
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return Settings.from_dict(data)

    return Settings()  # Return defaults
