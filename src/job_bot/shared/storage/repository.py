"""Base repository class for shared storage."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from .database import Database

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """Base class for all repositories."""

    def __init__(self, database: Database) -> None:
        self._db = database

    @property
    def db(self) -> Database:
        return self._db

    @abstractmethod
    def create(self, entity: T) -> T:
        """Create a new entity."""
        ...

    @abstractmethod
    def get_by_id(self, entity_id: Any) -> T | None:
        """Get entity by ID."""
        ...

    @abstractmethod
    def update(self, entity: T) -> T:
        """Update an existing entity."""
        ...

    @abstractmethod
    def delete(self, entity_id: Any) -> bool:
        """Delete entity by ID."""
        ...

    def _execute(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Execute a query and return all rows."""
        with self._db.connect() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchall()

    def _execute_one(
        self, query: str, params: tuple = ()
    ) -> sqlite3.Row | None:
        """Execute a query and return one row."""
        with self._db.connect() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchone()

    def _execute_write(self, query: str, params: tuple = ()) -> int:
        """Execute a write query and return rowcount."""
        with self._db.connect() as conn:
            cursor = conn.execute(query, params)
            conn.commit()
            return cursor.rowcount
