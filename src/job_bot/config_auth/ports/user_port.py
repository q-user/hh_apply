"""Port for user management operations - interface for other slices."""

from __future__ import annotations

from typing import Protocol

from job_bot.config_auth.models.user import UserProfile


class UserPort(Protocol):
    """Port for user profile CRUD."""

    def save_user(self, user: UserProfile) -> UserProfile:
        """Insert or update a user."""
        ...

    def get_user(self, user_id: str) -> UserProfile | None:
        """Return the user with the given id, or ``None``."""
        ...

    def get_user_by_profile(self, profile_id: str) -> UserProfile | None:
        """Return the user linked to ``profile_id``, or ``None``."""
        ...

    def list_users(self, profile_id: str | None = None) -> list[UserProfile]:
        """List users, optionally filtered by ``profile_id``."""
        ...

    def delete_user(self, user_id: str) -> bool:
        """Delete a user by id. Returns ``True`` if removed."""
        ...
