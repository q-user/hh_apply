"""User profile domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class UserProfile:
    """A user profile, optionally linked to a config profile_id.

    The same physical user can be associated with multiple config profiles
    (e.g. one for ``prod`` and one for ``dev``); the ``profile_id`` field
    makes the link explicit.
    """

    id: str
    full_name: str = ""
    hh_user_id: str | None = None
    email: str | None = None
    phone: str | None = None
    profile_id: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "id": self.id,
            "full_name": self.full_name,
            "hh_user_id": self.hh_user_id,
            "email": self.email,
            "phone": self.phone,
            "profile_id": self.profile_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserProfile:
        """Build an instance from a dict."""
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        return cls(
            id=data["id"],
            full_name=data.get("full_name", "") or "",
            hh_user_id=data.get("hh_user_id"),
            email=data.get("email"),
            phone=data.get("phone"),
            profile_id=data.get("profile_id"),
            created_at=(
                _coerce_datetime(created_at) if created_at else datetime.now()
            ),
            updated_at=(
                _coerce_datetime(updated_at) if updated_at else datetime.now()
            ),
            metadata=data.get("metadata") or {},
        )


def _coerce_datetime(value: Any) -> datetime:
    """Best-effort conversion of strings/dates to ``datetime``."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now()
    return datetime.now()
