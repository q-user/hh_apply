"""User handler — store and retrieve :class:`UserProfile` rows.

The handler owns the ``users`` SQLite table. The schema is intentionally
small — just the columns we need to look users up by id or by
``profile_id``. Metadata is stored as a JSON blob.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from job_bot.config_auth.models.user import UserProfile
from job_bot.shared.storage.database import Database


class UserHandler:
    """CRUD for :class:`UserProfile`."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._init_table()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_user(self, user: UserProfile) -> UserProfile:
        """Insert or update a user. ``updated_at`` is bumped on every save."""
        user.updated_at = datetime.now()
        row = self._user_to_row(user)
        with self._db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users"
                " (id, hh_user_id, full_name, email, phone, profile_id,"
                "  created_at, updated_at, metadata)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row.values()),
            )
            conn.commit()
        return user

    def get_user(self, user_id: str) -> UserProfile | None:
        """Return the user with the given id, or ``None``."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_profile(self, profile_id: str) -> UserProfile | None:
        """Return the first user linked to ``profile_id`` (or ``None``).

        HH.ru identifies users globally by ``hh_user_id``; we treat the
        ``profile_id`` link as a 1:1 mapping for simplicity (one user
        per HH profile).
        """
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE profile_id = ?"
                " ORDER BY updated_at DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self, profile_id: str | None = None) -> list[UserProfile]:
        """List users, optionally filtered by ``profile_id``."""
        with self._db.connect() as conn:
            if profile_id is not None:
                rows = conn.execute(
                    "SELECT * FROM users WHERE profile_id = ?"
                    " ORDER BY updated_at DESC",
                    (profile_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM users ORDER BY updated_at DESC"
                ).fetchall()
        return [self._row_to_user(r) for r in rows if r is not None]

    def delete_user(self, user_id: str) -> bool:
        """Delete a user by id. Returns ``True`` if a row was removed."""
        with self._db.connect() as conn:
            cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_table(self) -> None:
        self._db.execute_script(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                hh_user_id TEXT,
                full_name TEXT NOT NULL DEFAULT '',
                email TEXT,
                phone TEXT,
                profile_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _user_to_row(user: UserProfile) -> dict[str, Any]:
        return {
            "id": user.id,
            "hh_user_id": user.hh_user_id,
            "full_name": user.full_name,
            "email": user.email,
            "phone": user.phone,
            "profile_id": user.profile_id,
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat(),
            "metadata": json.dumps(user.metadata or {}),
        }

    @staticmethod
    def _row_to_user(row: Any) -> UserProfile:
        # ``sqlite3.Row`` supports dict-style access, so we can hand it
        # straight to ``UserProfile.from_dict`` after JSON-decoding metadata.
        data = dict(row)
        raw_meta = data.get("metadata") or "{}"
        if isinstance(raw_meta, str):
            try:
                data["metadata"] = json.loads(raw_meta)
            except json.JSONDecodeError:
                data["metadata"] = {}
        else:
            data["metadata"] = raw_meta or {}
        return UserProfile.from_dict(data)
