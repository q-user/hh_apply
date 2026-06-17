"""Auth handler — persist and refresh OAuth credentials.

Credentials are stored in a tiny ``oauth_credentials`` SQLite table that
contains a single row per ``profile_id``. This mirrors the
key/value pattern used by ``hh_applicant_tool``'s ``SettingsRepository``
but uses a typed schema so we can store all three OAuth fields and
support multi-profile tokens.

The handler keeps a thin wrapper around the :class:`Database` rather
than a separate repository class: the table is small, the access
patterns are simple, and keeping everything in one place makes the
slice easier to read.
"""

from __future__ import annotations

from typing import Callable

from job_bot.config_auth.models.credentials import OAuthCredentials
from job_bot.shared.storage.database import Database

# SQLite stores no enums; we use a sentinel "default" profile id when
# none is provided so a default token survives the lifecycle of the
# app.
DEFAULT_PROFILE_ID = "default"

RefreshFn = Callable[[str], OAuthCredentials]


class AuthHandler:
    """OAuth credentials persistence + refresh helper."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._init_table()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get_credentials(
        self, profile_id: str = DEFAULT_PROFILE_ID
    ) -> OAuthCredentials | None:
        """Return the stored credentials for ``profile_id`` (or ``None``)."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT access_token, refresh_token, access_expires_at"
                " FROM oauth_credentials WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        if not row:
            return None
        return OAuthCredentials(
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            access_expires_at=row["access_expires_at"],
        )

    def save_credentials(
        self,
        credentials: OAuthCredentials,
        profile_id: str = DEFAULT_PROFILE_ID,
    ) -> None:
        """Persist ``credentials`` for the given ``profile_id`` (overwrites)."""
        with self._db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_credentials"
                " (profile_id, access_token, refresh_token, access_expires_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    profile_id,
                    credentials.access_token,
                    credentials.refresh_token,
                    credentials.access_expires_at,
                ),
            )
            conn.commit()

    def clear_credentials(self, profile_id: str = DEFAULT_PROFILE_ID) -> None:
        """Delete stored credentials for ``profile_id`` (idempotent)."""
        with self._db.connect() as conn:
            conn.execute(
                "DELETE FROM oauth_credentials WHERE profile_id = ?",
                (profile_id,),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(
        self,
        refresher: RefreshFn,
        profile_id: str = DEFAULT_PROFILE_ID,
    ) -> OAuthCredentials:
        """Refresh the stored credentials via ``refresher``.

        ``refresher`` is any callable that takes the current
        ``refresh_token`` and returns a fresh :class:`OAuthCredentials`.
        The new credentials are persisted before being returned.
        """
        current = self.get_credentials(profile_id)
        if current is None or not current.refresh_token:
            raise ValueError(
                f"No refresh token available for profile '{profile_id}'."
            )
        new_creds = refresher(current.refresh_token)
        self.save_credentials(new_creds, profile_id)
        return new_creds

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_table(self) -> None:
        """Create the ``oauth_credentials`` table if it doesn't exist."""
        self._db.execute_script(
            """
            CREATE TABLE IF NOT EXISTS oauth_credentials (
                profile_id TEXT PRIMARY KEY,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                access_expires_at INTEGER NOT NULL
            )
            """
        )
