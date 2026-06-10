"""Port for relevance analysis operations - used by other slices."""

from __future__ import annotations

from typing import Any, Protocol

from job_bot.application_prep.models.relevance import RelevanceResult


class RelevancePort(Protocol):
    """Port for relevance analysis operations."""

    def analyze_resume_heavy(self, resume: dict[str, Any]) -> str:
        """Heavy resume analysis (full text + experience). Result is cached."""
        ...

    def analyze_resume_light(self, resume: dict[str, Any]) -> str:
        """Light resume analysis (title + skill_set only). Result is cached."""
        ...

    def is_suitable_heavy(self, vacancy: dict[str, Any]) -> RelevanceResult:
        """Heavy AI suitability check (with full vacancy description)."""
        ...

    def is_suitable_light(self, vacancy: dict[str, Any]) -> RelevanceResult:
        """Light AI suitability check (without description)."""
        ...

    def get_vacancy_key_skills(self, vacancy_id: str | int) -> str:
        """Get vacancy key skills as a single string."""
        ...

    def build_vacancy_context(
        self,
        vacancy: dict[str, Any],
        *,
        full_vacancy: dict[str, Any] | None = None,
        include_full: bool = False,
    ) -> str:
        """Build vacancy context text for prompt."""
        ...


class RelevanceStoragePort(Protocol):
    """Port for persisting relevance analysis results."""

    def save_analysis(self, draft_id: str, result: RelevanceResult) -> None:
        """Save relevance analysis linked to draft."""
        ...

    def get_analysis(self, draft_id: str) -> RelevanceResult | None:
        """Get relevance analysis by draft ID."""
        ...
