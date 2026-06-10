"""Config & Auth slice — configuration, OAuth credentials, user management.

This slice is the smallest in the project (no external APIs), so it is
extracted first to set the pattern for the rest of the VSA migration.
"""

from .handlers import (
    DEFAULT_PROFILE_ID,
    AuthHandler,
    ConfigHandler,
    UserHandler,
)
from .models import (
    AIClientConfig,
    AppConfig,
    HHConfig,
    MaxConfig,
    OAuthCredentials,
    SMTPConfig,
    TelegramConfig,
    UserProfile,
)
from .ports import AuthPort, ConfigPort, UserPort
from .slice import ConfigAuthSlice, create_config_auth_slice

__all__ = [
    # Models
    "AIClientConfig",
    "AppConfig",
    "HHConfig",
    "MaxConfig",
    "OAuthCredentials",
    "SMTPConfig",
    "TelegramConfig",
    "UserProfile",
    # Ports
    "AuthPort",
    "ConfigPort",
    "UserPort",
    # Handlers
    "AuthHandler",
    "ConfigHandler",
    "DEFAULT_PROFILE_ID",
    "UserHandler",
    # Slice
    "ConfigAuthSlice",
    "create_config_auth_slice",
]
