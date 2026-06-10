"""Database connection and session management for shared storage."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


class Database:
    """SQLite database connection manager."""

    def __init__(self, db_path: Path | str) -> None:
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
