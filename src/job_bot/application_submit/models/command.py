"""ApplyToVacanciesCommand DTO (issue #158).

Mirrors the legacy ``hh_applicant_tool.application.dto.ApplyToVacanciesCommand``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ApplyToVacanciesCommand:
    """Input to the apply-to-vacancies pipeline (issue #158).

    Constructed identically from the CLI, the UI and the worker / bot —
    no argparse / no service-locator dependency.
    """

    resume_id: str | None = None
    search: str | None = None
    search_params: dict[str, Any] = field(default_factory=dict)
    per_page: int = 100
    total_pages: int = 20
    dry_run: bool = False
    force_message: bool = False
    use_ai: bool = False
    ai_filter: Literal["heavy", "light"] | None = None
    ai_rate_limit: int = 40
    skip_tests: bool = False
    send_email: bool = False
    excluded_filter: str | None = None
    system_prompt: str = ""
    message_prompt: str = ""
    letter_file_content: str | None = None
    order_by: str | None = None
    relevance_rules: dict[str, Any] | None = None
    max_responses: int | None = None


__all__ = ["ApplyToVacanciesCommand"]
