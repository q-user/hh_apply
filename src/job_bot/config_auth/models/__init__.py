"""Domain models for the config_auth slice."""

from .config import (
    AIClientConfig,
    AppConfig,
    HHConfig,
    MaxConfig,
    SMTPConfig,
    TelegramConfig,
)
from .credentials import OAuthCredentials
from .user import UserProfile

__all__ = [
    # Config
    "AIClientConfig",
    "AppConfig",
    "HHConfig",
    "MaxConfig",
    "SMTPConfig",
    "TelegramConfig",
    # Auth
    "OAuthCredentials",
    # User
    "UserProfile",
]
