"""Search Profile repository."""

from __future__ import annotations

from typing import Any

from job_bot.shared.storage.database import Database
from job_bot.shared.storage.repository import BaseSqliteRepository
from job_bot.vacancy_search.models.search_profile import (
    SearchProfile,
    SearchProfileUpdate,
)


class SearchProfileRepository(BaseSqliteRepository):
    """Repository for search profiles."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._init_table()

    def _init_table(self) -> None:
        """Initialize the search_profiles table."""
        self._db.execute_script("""
            CREATE TABLE IF NOT EXISTS search_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                keywords TEXT DEFAULT '',
                schedule TEXT DEFAULT '[]',
                experience TEXT DEFAULT '[]',
                employment TEXT DEFAULT '[]',
                area TEXT DEFAULT '[]',
                salary INTEGER,
                currency TEXT DEFAULT 'RUR',
                only_with_salary INTEGER DEFAULT 0,
                search_period INTEGER DEFAULT 7,
                per_page INTEGER DEFAULT 100,
                page INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                metadata TEXT DEFAULT '{}'
            )
        """)

    def _row_to_profile(self, row: Any) -> SearchProfile:
        """Convert database row to SearchProfile."""
        import json

        return SearchProfile(
            id=row["id"],
            name=row["name"],
            keywords=row["keywords"],
            schedule=json.loads(row["schedule"]),
            experience=json.loads(row["experience"]),
            employment=json.loads(row["employment"]),
            area=json.loads(row["area"]),
            salary=row["salary"],
            currency=row["currency"],
            only_with_salary=bool(row["only_with_salary"]),
            search_period=row["search_period"],
            per_page=row["per_page"],
            page=row["page"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_active=bool(row["is_active"]),
            metadata=json.loads(row["metadata"]),
        )

    def _profile_to_row(self, profile: SearchProfile) -> dict[str, Any]:
        """Convert SearchProfile to database row dict."""
        import json
        from datetime import datetime

        def to_iso(val: Any) -> str:
            if isinstance(val, datetime):
                return val.isoformat()
            if isinstance(val, str):
                return val
            return str(val)

        return {
            "id": profile.id,
            "name": profile.name,
            "keywords": profile.keywords,
            "schedule": json.dumps(profile.schedule),
            "experience": json.dumps(profile.experience),
            "employment": json.dumps(profile.employment),
            "area": json.dumps(profile.area),
            "salary": profile.salary,
            "currency": profile.currency,
            "only_with_salary": int(profile.only_with_salary),
            "search_period": profile.search_period,
            "per_page": profile.per_page,
            "page": profile.page,
            "created_at": to_iso(profile.created_at),
            "updated_at": to_iso(profile.updated_at),
            "is_active": int(profile.is_active),
            "metadata": json.dumps(profile.metadata),
        }

    def create(self, entity: SearchProfile) -> SearchProfile:
        """Create a new search profile."""
        row = self._profile_to_row(entity)
        columns = ", ".join(row.keys())
        placeholders = ", ".join(["?"] * len(row))
        query = (
            f"INSERT INTO search_profiles ({columns}) VALUES ({placeholders})"
        )
        self._execute_write(query, tuple(row.values()))
        return entity

    def get_by_id(self, entity_id: str) -> SearchProfile | None:
        """Get search profile by ID."""
        row = self._execute_one(
            "SELECT * FROM search_profiles WHERE id = ?", (entity_id,)
        )
        return self._row_to_profile(row) if row else None

    def get_by_name(self, name: str) -> SearchProfile | None:
        """Get search profile by name."""
        row = self._execute_one(
            "SELECT * FROM search_profiles WHERE name = ?", (name,)
        )
        return self._row_to_profile(row) if row else None

    def get_all(self, active_only: bool = False) -> list[SearchProfile]:
        """Get all search profiles."""
        query = "SELECT * FROM search_profiles"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY created_at DESC"
        rows = self._execute(query)
        return [self._row_to_profile(row) for row in rows]

    def update(self, entity: SearchProfile) -> SearchProfile:
        """Update an existing search profile."""
        row = self._profile_to_row(entity)
        # Remove id from update
        row.pop("id", None)
        row.pop("created_at", None)
        set_clause = ", ".join([f"{k} = ?" for k in row.keys()])
        query = f"UPDATE search_profiles SET {set_clause} WHERE id = ?"
        params = tuple(row.values()) + (entity.id,)
        self._execute_write(query, params)
        return entity

    def delete(self, entity_id: str) -> bool:
        """Delete search profile by ID."""
        rowcount = self._execute_write(
            "DELETE FROM search_profiles WHERE id = ?", (entity_id,)
        )
        return rowcount > 0

    def apply_update(
        self, profile_id: str, update: SearchProfileUpdate
    ) -> SearchProfile | None:
        """Apply an update to a profile."""
        profile = self.get_by_id(profile_id)
        if not profile:
            return None
        updated = update.apply_to(profile)
        return self.update(updated)
