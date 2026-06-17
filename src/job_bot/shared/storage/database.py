"""Database connection and session management for shared storage."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import Mock


def validate_db_path(db_path: object) -> None:
    """Reject ``Mock`` / non-path values for ``db_path``.

    Regression guard for issue #78: a ``unittest.mock.MagicMock`` instance
    once leaked into a real :class:`Database` call. ``Path(mock).parent.mkdir(...)``
    coerced the mock to its class name (``"MagicMock"``) and created a stray
    ``./MagicMock/`` tree on disk. This guard fails fast with a clear error
    before any filesystem side effects.

    Accepted: :class:`str`, :class:`pathlib.Path`, any :class:`os.PathLike`.
    Rejected: ``unittest.mock.Mock`` (and subclasses MagicMock, AsyncMock,
    PropertyMock) and arbitrary types (int, float, None, list, dict, object,
    ``bytes`` — SQLite rejects a ``bytes`` path with an opaque error, so we
    surface a clearer error here).

    Note: callers in the ``Database(Path(db_path))`` pattern must invoke
    this on the *raw* input, before the ``Path(...)`` coercion —
    ``Path(MagicMock())`` succeeds and returns ``Path("MagicMock")``.
    """
    if isinstance(db_path, Mock):
        raise TypeError(
            f"db_path must be a real Path or str, got a Mock ({db_path!r}). "
            "Pass a real filesystem path or use an in-memory ':memory:' "
            "connection. Did a test double leak into production code?"
        )
    if not isinstance(db_path, (str, Path, os.PathLike)):
        raise TypeError(
            f"db_path must be a real Path or str, got {type(db_path).__name__}: {db_path!r}"
        )


class Database:
    """SQLite database connection manager."""

    def __init__(self, db_path: Path | str) -> None:
        validate_db_path(db_path)
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def execute_script(self, script: str) -> None:
        """Execute a SQL script (multiple statements)."""
        with self.connect() as conn:
            conn.executescript(script)

    @property
    def path(self) -> Path:
        return self._db_path


def create_database(db_path: Path | str) -> Database:
    """Factory function to create a Database instance."""
    return Database(db_path)
