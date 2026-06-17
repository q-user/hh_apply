"""PrepareVacanciesCommand DTO (issue #158).

Mirrors the legacy ``hh_applicant_tool.application.dto.PrepareVacanciesCommand``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PrepareVacanciesCommand:
    """Input to the prepare-vacancies pipeline (issue #158)."""

    search_profile: str | None = None
    dry_run: bool = False
    per_page: int = 100
    total_pages: int = 20
    force_message: bool = True
    system_prompt: str = ""
    ai_rate_limit: int = 40


__all__ = ["PrepareVacanciesCommand"]
