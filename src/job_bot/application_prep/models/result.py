"""PrepareVacanciesResult DTO (issue #158).

Mirrors the legacy ``hh_applicant_tool.application.dto.PrepareVacanciesResult``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PrepareVacanciesResult:
    """Stats returned by the prepare-vacancies pipeline (issue #158)."""

    profiles_processed: int = 0
    vacancies_seen: int = 0
    prepared: int = 0
    rejected: int = 0
    skipped: int = 0
    test_answers: int = 0
    failed: int = 0


__all__ = ["PrepareVacanciesResult"]
