"""Storage facade - aggregates all repositories for easy access."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .database import Database


@dataclass
class StorageFacade:
    """Aggregates all repositories for a slice to use."""

    database: Database

    # Repositories will be added as they are created
    # search_profiles: SearchProfileRepository | None = None
    # vacancies: VacancyRepository | None = None
    # etc.


def create_storage_facade(db_path: str | Path) -> StorageFacade:
    """Factory function to create a StorageFacade with database."""
    database = Database(Path(db_path))
    return StorageFacade(database=database)
