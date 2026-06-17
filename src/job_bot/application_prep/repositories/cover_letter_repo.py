"""Cover letter repository."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from job_bot.application_prep.models.cover_letter import (
    CoverLetter,
    CoverLetterCreate,
)
from job_bot.shared.storage.database import Database
from job_bot.shared.storage.repository import BaseSqliteRepository


class CoverLetterRepository(BaseSqliteRepository):
    """Repository for cover letters."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._init_table()

    def _init_table(self) -> None:
        """Initialize the cover_letters table."""
        self._db.execute_script("""
            CREATE TABLE IF NOT EXISTS cover_letters (
                id TEXT PRIMARY KEY,
                draft_id TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'generated',
                template_used TEXT,
                ai_generated INTEGER DEFAULT 0,
                placeholders TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._db.execute_script("""
            CREATE INDEX IF NOT EXISTS idx_cover_letters_draft_id
            ON cover_letters (draft_id)
        """)

    def _row_to_cover_letter(self, row: Any) -> CoverLetter:
        """Convert database row to CoverLetter."""
        return CoverLetter(
            id=row["id"],
            draft_id=row["draft_id"],
            content=row["content"],
            status=row["status"],
            template_used=row["template_used"],
            ai_generated=bool(row["ai_generated"]),
            placeholders=json.loads(row["placeholders"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _cover_letter_to_row(self, cover_letter: CoverLetter) -> dict[str, Any]:
        """Convert CoverLetter to database row dict."""

        def to_iso(val: Any) -> str:
            if isinstance(val, datetime):
                return val.isoformat()
            if isinstance(val, str):
                return val
            return str(val)

        return {
            "id": cover_letter.id,
            "draft_id": cover_letter.draft_id,
            "content": cover_letter.content,
            "status": cover_letter.status,
            "template_used": cover_letter.template_used,
            "ai_generated": 1 if cover_letter.ai_generated else 0,
            "placeholders": json.dumps(cover_letter.placeholders),
            "created_at": to_iso(cover_letter.created_at),
            "updated_at": to_iso(cover_letter.updated_at),
        }

    def create(self, entity: CoverLetter) -> CoverLetter:
        """Create a new cover letter."""
        row = self._cover_letter_to_row(entity)
        columns = ", ".join(row.keys())
        placeholders = ", ".join(["?"] * len(row))
        query = f"INSERT INTO cover_letters ({columns}) VALUES ({placeholders})"
        self._execute_write(query, tuple(row.values()))
        return entity

    def get_by_id(self, entity_id: str) -> CoverLetter | None:
        """Get cover letter by ID."""
        row = self._execute_one(
            "SELECT * FROM cover_letters WHERE id = ?", (entity_id,)
        )
        return self._row_to_cover_letter(row) if row else None

    def get_by_draft_id(self, draft_id: str) -> CoverLetter | None:
        """Get cover letter by draft ID."""
        row = self._execute_one(
            "SELECT * FROM cover_letters WHERE draft_id = ? LIMIT 1",
            (draft_id,),
        )
        return self._row_to_cover_letter(row) if row else None

    def list_all(self, limit: int = 100, offset: int = 0) -> list[CoverLetter]:
        """List all cover letters with pagination."""
        rows = self._execute(
            "SELECT * FROM cover_letters ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_cover_letter(row) for row in rows]

    def update(self, entity: CoverLetter) -> CoverLetter:
        """Update an existing cover letter."""
        entity.updated_at = datetime.now()
        row = self._cover_letter_to_row(entity)
        row.pop("id", None)
        row.pop("created_at", None)
        set_clause = ", ".join([f"{k} = ?" for k in row.keys()])
        query = f"UPDATE cover_letters SET {set_clause} WHERE id = ?"
        params = tuple(row.values()) + (entity.id,)
        self._execute_write(query, params)
        return entity

    def delete(self, entity_id: str) -> bool:
        """Delete cover letter by ID."""
        rowcount = self._execute_write(
            "DELETE FROM cover_letters WHERE id = ?", (entity_id,)
        )
        return rowcount > 0

    def delete_by_draft_id(self, draft_id: str) -> bool:
        """Delete cover letter by draft ID."""
        rowcount = self._execute_write(
            "DELETE FROM cover_letters WHERE draft_id = ?", (draft_id,)
        )
        return rowcount > 0

    def save(self, cover_letter_create: CoverLetterCreate) -> CoverLetter:
        """Save or update cover letter (upsert by draft_id)."""
        existing = self.get_by_draft_id(cover_letter_create.draft_id)
        if existing is not None:
            existing.content = cover_letter_create.content
            existing.status = cover_letter_create.status
            existing.template_used = cover_letter_create.template_used
            existing.ai_generated = cover_letter_create.ai_generated
            existing.placeholders = cover_letter_create.placeholders
            return self.update(existing)
        return self.create(cover_letter_create.to_cover_letter())
