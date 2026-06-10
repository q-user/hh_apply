"""Vacancy repository."""

from __future__ import annotations

from typing import Any

from job_bot.shared.storage.database import Database
from job_bot.shared.storage.repository import BaseRepository
from job_bot.vacancy_search.models.vacancy import Vacancy


class VacancyRepository(BaseRepository[Vacancy]):
    """Repository for vacancies."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._init_table()

    def _init_table(self) -> None:
        """Initialize the vacancies table."""
        self._db.execute_script("""
            CREATE TABLE IF NOT EXISTS vacancies (
                id TEXT PRIMARY KEY,
                hh_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                employer_name TEXT NOT NULL,
                employer_id TEXT,
                area_name TEXT DEFAULT '',
                salary_from INTEGER,
                salary_to INTEGER,
                currency TEXT DEFAULT 'RUR',
                experience TEXT DEFAULT '',
                employment TEXT DEFAULT '',
                schedule TEXT DEFAULT '',
                description TEXT DEFAULT '',
                key_skills TEXT DEFAULT '[]',
                published_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                raw_data TEXT DEFAULT '{}'
            )
        """)

    def _row_to_vacancy(self, row: Any) -> Vacancy:
        """Convert database row to Vacancy."""
        import json

        return Vacancy(
            id=row["id"],
            hh_id=row["hh_id"],
            name=row["name"],
            employer_name=row["employer_name"],
            employer_id=row["employer_id"],
            area_name=row["area_name"],
            salary_from=row["salary_from"],
            salary_to=row["salary_to"],
            currency=row["currency"],
            experience=row["experience"],
            employment=row["employment"],
            schedule=row["schedule"],
            description=row["description"],
            key_skills=json.loads(row["key_skills"]),
            published_at=row["published_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            raw_data=json.loads(row["raw_data"]),
        )

    def _vacancy_to_row(self, vacancy: Vacancy) -> dict[str, Any]:
        """Convert Vacancy to database row dict."""
        import json
        from datetime import datetime

        def to_iso(val: Any) -> str | None:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val.isoformat()
            if isinstance(val, str):
                return val
            return str(val)

        return {
            "id": vacancy.id,
            "hh_id": vacancy.hh_id,
            "name": vacancy.name,
            "employer_name": vacancy.employer_name,
            "employer_id": vacancy.employer_id,
            "area_name": vacancy.area_name,
            "salary_from": vacancy.salary_from,
            "salary_to": vacancy.salary_to,
            "currency": vacancy.currency,
            "experience": vacancy.experience,
            "employment": vacancy.employment,
            "schedule": vacancy.schedule,
            "description": vacancy.description,
            "key_skills": json.dumps(vacancy.key_skills),
            "published_at": to_iso(vacancy.published_at),
            "created_at": to_iso(vacancy.created_at),
            "updated_at": to_iso(vacancy.updated_at),
            "raw_data": json.dumps(vacancy.raw_data),
        }

    def create(self, entity: Vacancy) -> Vacancy:
        """Create a new vacancy."""
        row = self._vacancy_to_row(entity)
        columns = ", ".join(row.keys())
        placeholders = ", ".join(["?"] * len(row))
        query = f"INSERT INTO vacancies ({columns}) VALUES ({placeholders})"
        self._execute_write(query, tuple(row.values()))
        return entity

    def get_by_id(self, entity_id: str) -> Vacancy | None:
        """Get vacancy by ID."""
        row = self._execute_one(
            "SELECT * FROM vacancies WHERE id = ?", (entity_id,)
        )
        return self._row_to_vacancy(row) if row else None

    def get_by_hh_id(self, hh_id: str) -> Vacancy | None:
        """Get vacancy by HH ID."""
        row = self._execute_one(
            "SELECT * FROM vacancies WHERE hh_id = ?", (hh_id,)
        )
        return self._row_to_vacancy(row) if row else None

    def get_all(self, limit: int = 100, offset: int = 0) -> list[Vacancy]:
        """Get all vacancies with pagination."""
        rows = self._execute(
            "SELECT * FROM vacancies ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_vacancy(row) for row in rows]

    def update(self, entity: Vacancy) -> Vacancy:
        """Update an existing vacancy."""
        row = self._vacancy_to_row(entity)
        row.pop("id", None)
        row.pop("created_at", None)
        set_clause = ", ".join([f"{k} = ?" for k in row.keys()])
        query = f"UPDATE vacancies SET {set_clause} WHERE id = ?"
        params = tuple(row.values()) + (entity.id,)
        self._execute_write(query, params)
        return entity

    def delete(self, entity_id: str) -> bool:
        """Delete vacancy by ID."""
        rowcount = self._execute_write(
            "DELETE FROM vacancies WHERE id = ?", (entity_id,)
        )
        return rowcount > 0

    def delete_by_hh_id(self, hh_id: str) -> bool:
        """Delete vacancy by HH ID."""
        rowcount = self._execute_write(
            "DELETE FROM vacancies WHERE hh_id = ?", (hh_id,)
        )
        return rowcount > 0

    def exists_by_hh_id(self, hh_id: str) -> bool:
        """Check if vacancy exists by HH ID."""
        row = self._execute_one(
            "SELECT 1 FROM vacancies WHERE hh_id = ? LIMIT 1", (hh_id,)
        )
        return row is not None

    def search(
        self,
        keywords: str | None = None,
        employer_name: str | None = None,
        area_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Vacancy]:
        """Search vacancies with filters."""
        conditions = []
        params: list[Any] = []

        if keywords:
            conditions.append("(name LIKE ? OR description LIKE ?)")
            params.extend([f"%{keywords}%", f"%{keywords}%"])

        if employer_name:
            conditions.append("employer_name LIKE ?")
            params.append(f"%{employer_name}%")

        if area_name:
            conditions.append("area_name LIKE ?")
            params.append(f"%{area_name}%")

        query = "SELECT * FROM vacancies"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._execute(query, tuple(params))
        return [self._row_to_vacancy(row) for row in rows]

    def count(self) -> int:
        """Count total vacancies."""
        row = self._execute_one("SELECT COUNT(*) as cnt FROM vacancies")
        return row["cnt"] if row else 0
