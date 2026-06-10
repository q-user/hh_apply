"""Relevance analysis repository."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from job_bot.application_prep.models.relevance import (
    RelevanceAnalysis,
    RelevanceResult,
)
from job_bot.shared.storage.database import Database
from job_bot.shared.storage.repository import BaseRepository


class RelevanceAnalysisRepository(BaseRepository[RelevanceAnalysis]):
    """Repository for relevance analyses."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._init_table()

    def _init_table(self) -> None:
        """Initialize the relevance_analyses table."""
        self._db.execute_script("""
            CREATE TABLE IF NOT EXISTS relevance_analyses (
                id TEXT PRIMARY KEY,
                draft_id TEXT NOT NULL,
                suitable INTEGER DEFAULT 1,
                relevance_score INTEGER,
                success_probability INTEGER,
                primary_stack TEXT DEFAULT '[]',
                secondary_stack TEXT DEFAULT '[]',
                project_summary TEXT,
                complexity TEXT,
                salary_summary TEXT,
                employment_format TEXT,
                perks TEXT DEFAULT '[]',
                risks TEXT DEFAULT '[]',
                reason TEXT,
                raw_response TEXT,
                applied_rules TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)
        self._db.execute_script("""
            CREATE INDEX IF NOT EXISTS idx_relevance_analyses_draft_id
            ON relevance_analyses (draft_id)
        """)

    def _row_to_analysis(self, row: Any) -> RelevanceAnalysis:
        """Convert database row to RelevanceAnalysis."""
        return RelevanceAnalysis(
            id=row["id"],
            draft_id=row["draft_id"],
            suitable=bool(row["suitable"]),
            relevance_score=row["relevance_score"],
            success_probability=row["success_probability"],
            primary_stack=json.loads(row["primary_stack"] or "[]"),
            secondary_stack=json.loads(row["secondary_stack"] or "[]"),
            project_summary=row["project_summary"],
            complexity=row["complexity"],
            salary_summary=row["salary_summary"],
            employment_format=row["employment_format"],
            perks=json.loads(row["perks"] or "[]"),
            risks=json.loads(row["risks"] or "[]"),
            reason=row["reason"],
            raw_response=row["raw_response"],
            applied_rules=json.loads(row["applied_rules"] or "{}"),
            created_at=row["created_at"],
        )

    def _analysis_to_row(self, analysis: RelevanceAnalysis) -> dict[str, Any]:
        """Convert RelevanceAnalysis to database row dict."""

        def to_iso(val: Any) -> str:
            if isinstance(val, datetime):
                return val.isoformat()
            if isinstance(val, str):
                return val
            return str(val)

        return {
            "id": analysis.id,
            "draft_id": analysis.draft_id,
            "suitable": 1 if analysis.suitable else 0,
            "relevance_score": analysis.relevance_score,
            "success_probability": analysis.success_probability,
            "primary_stack": json.dumps(analysis.primary_stack),
            "secondary_stack": json.dumps(analysis.secondary_stack),
            "project_summary": analysis.project_summary,
            "complexity": analysis.complexity,
            "salary_summary": analysis.salary_summary,
            "employment_format": analysis.employment_format,
            "perks": json.dumps(analysis.perks),
            "risks": json.dumps(analysis.risks),
            "reason": analysis.reason,
            "raw_response": analysis.raw_response,
            "applied_rules": json.dumps(analysis.applied_rules),
            "created_at": to_iso(analysis.created_at),
        }

    def create(self, entity: RelevanceAnalysis) -> RelevanceAnalysis:
        """Create a new relevance analysis."""
        row = self._analysis_to_row(entity)
        columns = ", ".join(row.keys())
        placeholders = ", ".join(["?"] * len(row))
        query = f"INSERT INTO relevance_analyses ({columns}) VALUES ({placeholders})"
        self._execute_write(query, tuple(row.values()))
        return entity

    def get_by_id(self, entity_id: str) -> RelevanceAnalysis | None:
        """Get relevance analysis by ID."""
        row = self._execute_one(
            "SELECT * FROM relevance_analyses WHERE id = ?", (entity_id,)
        )
        return self._row_to_analysis(row) if row else None

    def get_by_draft_id(self, draft_id: str) -> RelevanceAnalysis | None:
        """Get relevance analysis by draft ID."""
        row = self._execute_one(
            "SELECT * FROM relevance_analyses WHERE draft_id = ? LIMIT 1",
            (draft_id,),
        )
        return self._row_to_analysis(row) if row else None

    def list_all(
        self, limit: int = 100, offset: int = 0
    ) -> list[RelevanceAnalysis]:
        """List all relevance analyses with pagination."""
        rows = self._execute(
            "SELECT * FROM relevance_analyses ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_analysis(row) for row in rows]

    def update(self, entity: RelevanceAnalysis) -> RelevanceAnalysis:
        """Update an existing relevance analysis."""
        row = self._analysis_to_row(entity)
        row.pop("id", None)
        row.pop("created_at", None)
        set_clause = ", ".join([f"{k} = ?" for k in row.keys()])
        query = f"UPDATE relevance_analyses SET {set_clause} WHERE id = ?"
        params = tuple(row.values()) + (entity.id,)
        self._execute_write(query, params)
        return entity

    def delete(self, entity_id: str) -> bool:
        """Delete relevance analysis by ID."""
        rowcount = self._execute_write(
            "DELETE FROM relevance_analyses WHERE id = ?", (entity_id,)
        )
        return rowcount > 0

    def delete_by_draft_id(self, draft_id: str) -> bool:
        """Delete relevance analysis by draft ID."""
        rowcount = self._execute_write(
            "DELETE FROM relevance_analyses WHERE draft_id = ?", (draft_id,)
        )
        return rowcount > 0

    def save_analysis(self, draft_id: str, result: RelevanceResult) -> None:
        """Save RelevanceResult for a draft (upsert by draft_id)."""
        existing = self.get_by_draft_id(draft_id)
        if existing is not None:
            existing.suitable = result.suitable
            existing.relevance_score = result.relevance_score
            existing.success_probability = result.success_probability
            existing.primary_stack = result.primary_stack or []
            existing.secondary_stack = result.secondary_stack or []
            existing.project_summary = result.project_summary
            existing.complexity = result.complexity
            existing.salary_summary = result.salary_summary
            existing.employment_format = result.employment_format
            existing.perks = result.perks or []
            existing.risks = result.risks or []
            existing.reason = result.reason
            existing.raw_response = result.raw_response
            existing.applied_rules = result.applied_rules or {}
            self.update(existing)
        else:
            self.create(RelevanceAnalysis.from_result(draft_id, result))

    def get_analysis(self, draft_id: str) -> RelevanceResult | None:
        """Get RelevanceResult by draft ID."""
        analysis = self.get_by_draft_id(draft_id)
        return analysis.to_result() if analysis else None
