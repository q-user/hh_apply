"""Application draft repository."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from job_bot.application_prep.models.application import (
    ApplicationDraft,
    ApplicationDraftCreate,
)
from job_bot.shared.storage.database import Database
from job_bot.shared.storage.repository import BaseSqliteRepository


class ApplicationDraftRepository(BaseSqliteRepository):
    """Repository for application drafts."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._init_table()

    def _init_table(self) -> None:
        """Initialize the application_drafts table."""
        self._db.execute_script("""
            CREATE TABLE IF NOT EXISTS application_drafts (
                id TEXT PRIMARY KEY,
                search_profile_id TEXT,
                resume_id TEXT NOT NULL,
                vacancy_id INTEGER NOT NULL,
                employer_id INTEGER,
                status TEXT DEFAULT 'prepared',
                relevance_score INTEGER,
                relevance_reason TEXT,
                analysis_json TEXT,
                full_vacancy_json TEXT DEFAULT '{}',
                cover_letter TEXT,
                cover_letter_status TEXT,
                has_test INTEGER DEFAULT 0,
                test_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._db.execute_script("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_application_drafts_resume_vacancy
            ON application_drafts (resume_id, vacancy_id)
        """)
        self._db.execute_script("""
            CREATE INDEX IF NOT EXISTS idx_application_drafts_status
            ON application_drafts (status)
        """)

    def _row_to_draft(self, row: Any) -> ApplicationDraft:
        """Convert database row to ApplicationDraft."""
        analysis_json_raw = row["analysis_json"]
        full_vacancy_raw = row["full_vacancy_json"]
        return ApplicationDraft(
            id=row["id"],
            search_profile_id=row["search_profile_id"],
            resume_id=row["resume_id"],
            vacancy_id=row["vacancy_id"],
            employer_id=row["employer_id"],
            status=row["status"],
            relevance_score=row["relevance_score"],
            relevance_reason=row["relevance_reason"],
            analysis_json=json.loads(analysis_json_raw)
            if analysis_json_raw
            else None,
            full_vacancy_json=json.loads(full_vacancy_raw or "{}"),
            cover_letter=row["cover_letter"],
            cover_letter_status=row["cover_letter_status"],
            has_test=bool(row["has_test"]),
            test_status=row["test_status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _draft_to_row(self, draft: ApplicationDraft) -> dict[str, Any]:
        """Convert ApplicationDraft to database row dict."""

        def to_iso(val: Any) -> str:
            if isinstance(val, datetime):
                return val.isoformat()
            if isinstance(val, str):
                return val
            return str(val)

        return {
            "id": draft.id,
            "search_profile_id": draft.search_profile_id,
            "resume_id": draft.resume_id,
            "vacancy_id": draft.vacancy_id,
            "employer_id": draft.employer_id,
            "status": draft.status,
            "relevance_score": draft.relevance_score,
            "relevance_reason": draft.relevance_reason,
            "analysis_json": (
                json.dumps(draft.analysis_json) if draft.analysis_json else None
            ),
            "full_vacancy_json": json.dumps(draft.full_vacancy_json),
            "cover_letter": draft.cover_letter,
            "cover_letter_status": draft.cover_letter_status,
            "has_test": 1 if draft.has_test else 0,
            "test_status": draft.test_status,
            "created_at": to_iso(draft.created_at),
            "updated_at": to_iso(draft.updated_at),
        }

    def create(self, entity: ApplicationDraft) -> ApplicationDraft:
        """Create a new application draft."""
        row = self._draft_to_row(entity)
        columns = ", ".join(row.keys())
        placeholders = ", ".join(["?"] * len(row))
        query = f"INSERT INTO application_drafts ({columns}) VALUES ({placeholders})"
        self._execute_write(query, tuple(row.values()))
        return entity

    def get_by_id(self, entity_id: str) -> ApplicationDraft | None:
        """Get application draft by ID."""
        row = self._execute_one(
            "SELECT * FROM application_drafts WHERE id = ?", (entity_id,)
        )
        return self._row_to_draft(row) if row else None

    def get_by_resume_vacancy(
        self, resume_id: str, vacancy_id: int
    ) -> ApplicationDraft | None:
        """Get application draft by resume_id + vacancy_id."""
        row = self._execute_one(
            "SELECT * FROM application_drafts WHERE resume_id = ? AND vacancy_id = ? LIMIT 1",
            (resume_id, vacancy_id),
        )
        return self._row_to_draft(row) if row else None

    def list_all(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ApplicationDraft]:
        """List application drafts with optional status filter."""
        if status:
            rows = self._execute(
                "SELECT * FROM application_drafts WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        else:
            rows = self._execute(
                "SELECT * FROM application_drafts ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._row_to_draft(row) for row in rows]

    def update(self, entity: ApplicationDraft) -> ApplicationDraft:
        """Update an existing application draft."""
        entity.updated_at = datetime.now()
        row = self._draft_to_row(entity)
        row.pop("id", None)
        row.pop("created_at", None)
        set_clause = ", ".join([f"{k} = ?" for k in row.keys()])
        query = f"UPDATE application_drafts SET {set_clause} WHERE id = ?"
        params = tuple(row.values()) + (entity.id,)
        self._execute_write(query, params)
        return entity

    def delete(self, entity_id: str) -> bool:
        """Delete application draft by ID."""
        rowcount = self._execute_write(
            "DELETE FROM application_drafts WHERE id = ?", (entity_id,)
        )
        return rowcount > 0

    def save(self, draft_create: ApplicationDraftCreate) -> ApplicationDraft:
        """Save or update application draft (upsert by resume_id+vacancy_id)."""
        existing = self.get_by_resume_vacancy(
            draft_create.resume_id, draft_create.vacancy_id
        )
        if existing is not None:
            existing.search_profile_id = draft_create.search_profile_id
            existing.employer_id = draft_create.employer_id
            existing.status = draft_create.status
            existing.relevance_score = draft_create.relevance_score
            existing.relevance_reason = draft_create.relevance_reason
            existing.analysis_json = draft_create.analysis_json
            existing.full_vacancy_json = draft_create.full_vacancy_json
            existing.cover_letter = draft_create.cover_letter
            existing.cover_letter_status = draft_create.cover_letter_status
            existing.has_test = draft_create.has_test
            existing.test_status = draft_create.test_status
            return self.update(existing)
        return self.create(draft_create.to_draft())

    def count(self) -> int:
        """Count total application drafts."""
        row = self._execute_one(
            "SELECT COUNT(*) as cnt FROM application_drafts"
        )
        return row["cnt"] if row else 0

    def count_by_status(self, status: str) -> int:
        """Count application drafts with a specific status."""
        row = self._execute_one(
            "SELECT COUNT(*) as cnt FROM application_drafts WHERE status = ?",
            (status,),
        )
        return row["cnt"] if row else 0
