"""Storage facade - aggregates all repositories for easy access."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .database import Database, validate_db_path


@dataclass
class StorageFacade:
    """Aggregates all repositories for a slice to use."""

    database: Database

    # Repositories will be added as they are created
    # search_profiles: SearchProfileRepository | None = None
    # vacancies: VacancyRepository | None = None
    # etc.


def create_storage_facade(db_path: str | Path) -> StorageFacade:
    """Factory function to create a StorageFacade with database.

    Issue #78: validate ``db_path`` *before* ``Path(db_path)`` so a
    ``unittest.mock.Mock`` fails fast instead of being silently coerced
    to its class-name string (``"MagicMock"``) and turned into a real
    filesystem directory.
    """
    validate_db_path(db_path)
    database = Database(Path(db_path))
    return StorageFacade(database=database)
