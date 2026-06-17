"""OAuth credentials domain model."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class OAuthCredentials:
    """OAuth2 credentials bundle for the HH.ru API.

    Mirrors the legacy ``AccessToken`` TypedDict used in
    ``hh_applicant_tool/api/datatypes.py`` and adds convenience helpers
    for expiration checks.
    """

    access_token: str = ""
    refresh_token: str = ""
    access_expires_at: int = 0  # Unix timestamp; 0 == unknown/expired

    @property
    def is_expired(self) -> bool:
        """True if the access token has expired (or is unset)."""
        return self._check_expiry(0)

    def is_expired_with_buffer(self, buffer_seconds: int = 60) -> bool:
        """True if the access token expired, or will expire within
        ``buffer_seconds`` from now.

        The default 60s buffer lets the caller refresh proactively before
        the token actually expires.
        """
        return self._check_expiry(int(buffer_seconds))

    def _check_expiry(self, buffer_seconds: int) -> bool:
        if not self.access_token or not self.access_expires_at:
            return True
        return int(time.time()) >= int(self.access_expires_at) - buffer_seconds

    @property
    def expires_in(self) -> int:
        """Seconds until the access token expires (negative if already expired)."""
        return int(self.access_expires_at) - int(time.time())

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (e.g. for JSON storage)."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_expires_at": self.access_expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthCredentials:
        """Build an instance from a dict, ignoring unknown fields."""
        return cls(
            access_token=data.get("access_token", "") or "",
            refresh_token=data.get("refresh_token", "") or "",
            access_expires_at=int(data.get("access_expires_at", 0) or 0),
        )
