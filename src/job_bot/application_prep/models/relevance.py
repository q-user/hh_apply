"""Relevance analysis domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass
class RelevanceResult:
    """Structured result of AI relevance filtering.

    Contains all fields that AI can return in the strict JSON contract,
    plus backward compatibility with old boolean-only format ({"suitable": true/false}).
    """

    suitable: bool
    relevance_score: int | None = None
    success_probability: int | None = None
    primary_stack: list[str] | None = None
    secondary_stack: list[str] | None = None
    project_summary: str | None = None
    complexity: str | None = None
    salary_summary: str | None = None
    employment_format: str | None = None
    perks: list[str] | None = None
    risks: list[str] | None = None
    reason: str | None = None
    raw_response: str | None = None
    # Applied profile rules - for debugging and logging.
    # Not written to to_analysis_json (these are profile-local metadata).
    applied_rules: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def score(self) -> int | None:
        """Backwards-compat alias for relevance_score."""
        return self.relevance_score

    def to_analysis_json(self) -> dict[str, Any]:
        """Return dict suitable for storage in application_drafts.analysis_json.

        None fields are dropped to avoid bloating JSON.
        raw_response is intentionally NOT included (it's a separate column/used separately for debugging).
        """
        data: dict[str, Any] = {"suitable": self.suitable}
        for key in (
            "relevance_score",
            "success_probability",
            "primary_stack",
            "secondary_stack",
            "project_summary",
            "complexity",
            "salary_summary",
            "employment_format",
            "perks",
            "risks",
            "reason",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


@dataclass
class RelevanceAnalysis:
    """Persisted relevance analysis linked to an application draft."""

    id: str = field(default_factory=lambda: str(uuid4()))
    draft_id: str = ""
    suitable: bool = True
    relevance_score: int | None = None
    success_probability: int | None = None
    primary_stack: list[str] = field(default_factory=list)
    secondary_stack: list[str] = field(default_factory=list)
    project_summary: str | None = None
    complexity: str | None = None
    salary_summary: str | None = None
    employment_format: str | None = None
    perks: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    reason: str | None = None
    raw_response: str | None = None
    applied_rules: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_result(
        cls, draft_id: str, result: RelevanceResult
    ) -> RelevanceAnalysis:
        """Create RelevanceAnalysis from RelevanceResult."""
        return cls(
            draft_id=draft_id,
            suitable=result.suitable,
            relevance_score=result.relevance_score,
            success_probability=result.success_probability,
            primary_stack=result.primary_stack or [],
            secondary_stack=result.secondary_stack or [],
            project_summary=result.project_summary,
            complexity=result.complexity,
            salary_summary=result.salary_summary,
            employment_format=result.employment_format,
            perks=result.perks or [],
            risks=result.risks or [],
            reason=result.reason,
            raw_response=result.raw_response,
            applied_rules=result.applied_rules or {},
        )

    def to_result(self) -> RelevanceResult:
        """Convert back to RelevanceResult."""
        return RelevanceResult(
            suitable=self.suitable,
            relevance_score=self.relevance_score,
            success_probability=self.success_probability,
            primary_stack=self.primary_stack or None,
            secondary_stack=self.secondary_stack or None,
            project_summary=self.project_summary,
            complexity=self.complexity,
            salary_summary=self.salary_summary,
            employment_format=self.employment_format,
            perks=self.perks or None,
            risks=self.risks or None,
            reason=self.reason,
            raw_response=self.raw_response,
            applied_rules=self.applied_rules or {},
        )


# Constants from original relevance.py
SCORE_MIN = 0
SCORE_MAX = 100
MAX_RETRIES = 3
