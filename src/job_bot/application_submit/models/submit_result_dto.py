"""ApplyToVacanciesResult DTO (issue #158).

Mirrors the legacy ``hh_applicant_tool.application.dto.ApplyToVacanciesResult``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApplyToVacanciesResult:
    """Stats returned by the apply-to-vacancies pipeline (issue #158)."""

    resumes_processed: int = 0
    vacancies_seen: int = 0
    skipped: int = 0
    applied: int = 0
    failed: int = 0
    limit_reached: bool = False


__all__ = ["ApplyToVacanciesResult"]
