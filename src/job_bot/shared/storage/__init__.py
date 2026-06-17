"""Shared storage layer - SQLite repositories and database utilities.

Issue #144 public surface:

- :class:`BaseSqliteRepository` -- the concrete SQLite base for VSA slice
  repositories (replaces the old ``ABC[Generic[T]]``).
- :class:`BaseRepository`       -- the new ``Protocol`` in
  :mod:`job_bot.shared.storage.ports` (canonical name for cross-slice
  consumers). Imported here as :data:`BaseRepository` for convenience.
- :class:`StoragePort`          -- the cross-slice facade Protocol.

The old concrete ``BaseRepository`` (in :mod:`.repository`) is kept as
a deprecated back-compat shim and will be removed in 2.0 (issue #156).
"""

from __future__ import annotations

from .database import Database, create_database, validate_db_path
from .ports import BaseRepository, EventBusPort, StoragePort
from .repository import BaseSqliteRepository

__all__ = [
    "BaseRepository",  # the new Protocol (canonical name)
    "BaseSqliteRepository",  # the new concrete base
    "Database",
    "EventBusPort",
    "StoragePort",
    "create_database",
    "validate_db_path",
]
