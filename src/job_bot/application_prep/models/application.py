"""Application draft domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass
class ApplicationDraft:
    """Application draft entity - represents a prepared application ready for review/send."""

    id: str = field(default_factory=lambda: str(uuid4()))
    search_profile_id: str | None = None
    resume_id: str = ""
    vacancy_id: int = 0
    employer_id: int | None = None
    status: str = "prepared"  # prepared, rejected, sent, archived
    relevance_score: int | None = None
    relevance_reason: str | None = None
    analysis_json: dict[str, Any] | None = None
    full_vacancy_json: dict[str, Any] = field(default_factory=dict)
    cover_letter: str | None = None
    cover_letter_status: str | None = None  # generated, failed
    has_test: bool = False
    test_status: str | None = None  # generated, manual_required
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def create_prepared(
        cls,
        *,
        resume_id: str,
        vacancy_id: int,
        employer_id: int | None = None,
        search_profile_id: str | None = None,
        relevance_score: int | None = None,
        relevance_reason: str | None = None,
        analysis_json: dict[str, Any] | None = None,
        full_vacancy_json: dict[str, Any] | None = None,
        cover_letter: str | None = None,
        cover_letter_status: str | None = None,
        has_test: bool = False,
        test_status: str | None = None,
        status: str = "prepared",
    ) -> ApplicationDraft:
        """Create a prepared application draft."""
        return cls(
            search_profile_id=search_profile_id,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            employer_id=employer_id,
            status=status,
            relevance_score=relevance_score,
            relevance_reason=relevance_reason,
            analysis_json=analysis_json,
            full_vacancy_json=full_vacancy_json or {},
            cover_letter=cover_letter,
            cover_letter_status=cover_letter_status,
            has_test=has_test,
            test_status=test_status,
        )

    @classmethod
    def create_rejected(
        cls,
        *,
        resume_id: str,
        vacancy_id: int,
        employer_id: int | None = None,
        search_profile_id: str | None = None,
        relevance_score: int | None = None,
        relevance_reason: str | None = None,
        analysis_json: dict[str, Any] | None = None,
        full_vacancy_json: dict[str, Any] | None = None,
    ) -> ApplicationDraft:
        """Create a rejected application draft."""
        return cls(
            search_profile_id=search_profile_id,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            employer_id=employer_id,
            status="rejected",
            relevance_score=relevance_score,
            relevance_reason=relevance_reason,
            analysis_json=analysis_json,
            full_vacancy_json=full_vacancy_json or {},
            cover_letter=None,
            cover_letter_status=None,
            has_test=bool(full_vacancy_json and full_vacancy_json.get("has_test")),
            test_status=None,
        )


@dataclass
class ApplicationDraftCreate:
    """Data for creating a new application draft."""

    search_profile_id: str | None = None
    resume_id: str = ""
    vacancy_id: int = 0
    employer_id: int | None = None
    status: str = "prepared"
    relevance_score: int | None = None
    relevance_reason: str | None = None
    analysis_json: dict[str, Any] | None = None
    full_vacancy_json: dict[str, Any] = field(default_factory=dict)
    cover_letter: str | None = None
    cover_letter_status: str | None = None
    has_test: bool = False
    test_status: str | None = None

    def to_draft(self) -> ApplicationDraft:
        """Convert to ApplicationDraft entity."""
        return ApplicationDraft(
            search_profile_id=self.search_profile_id,
            resume_id=self.resume_id,
            vacancy_id=self.vacancy_id,
            employer_id=self.employer_id,
            status=self.status,
            relevance_score=self.relevance_score,
            relevance_reason=self.relevance_reason,
            analysis_json=self.analysis_json,
            full_vacancy_json=self.full_vacancy_json,
            cover_letter=self.cover_letter,
            cover_letter_status=self.cover_letter_status,
            has_test=self.has_test,
            test_status=self.test_status,
        )
