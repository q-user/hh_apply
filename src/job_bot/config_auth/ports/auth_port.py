"""Port for auth (OAuth) operations - interface for other slices."""

from __future__ import annotations

from typing import Callable, Protocol

from job_bot.config_auth.models.credentials import OAuthCredentials


class AuthPort(Protocol):
    """Port for OAuth credentials persistence + refresh."""

    def get_credentials(
        self, profile_id: str = "default"
    ) -> OAuthCredentials | None:
        """Return the stored credentials for ``profile_id`` (or ``None``)."""
        ...

    def save_credentials(
        self,
        credentials: OAuthCredentials,
        profile_id: str = "default",
    ) -> None:
        """Persist ``credentials`` for the given ``profile_id``."""
        ...

    def clear_credentials(self, profile_id: str = "default") -> None:
        """Delete stored credentials for ``profile_id``."""
        ...

    def refresh(
        self,
        refresher: Callable[[str], OAuthCredentials],
        profile_id: str = "default",
    ) -> OAuthCredentials:
        """Refresh the stored credentials via ``refresher``."""
        ...
