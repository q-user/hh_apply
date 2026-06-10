"""Port for vacancy operations - used by other slices."""

from __future__ import annotations

from typing import Protocol

from job_bot.vacancy_search.models.vacancy import Vacancy, VacancyCreate


class VacancyPort(Protocol):
    """Port for vacancy operations."""

    def create_vacancy(self, vacancy: VacancyCreate) -> Vacancy:
        """Create a new vacancy record."""
        ...

    def get_vacancy(self, vacancy_id: str) -> Vacancy | None:
        """Get a vacancy by ID."""
        ...

    def get_vacancy_by_hh_id(self, hh_id: str) -> Vacancy | None:
        """Get a vacancy by HH ID."""
        ...

    def list_vacancies(
        self, limit: int = 100, offset: int = 0
    ) -> list[Vacancy]:
        """List vacancies with pagination."""
        ...

    def search_vacancies(
        self,
        keywords: str | None = None,
        employer_name: str | None = None,
        area_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Vacancy]:
        """Search vacancies with filters."""
        ...

    def update_vacancy(self, vacancy: Vacancy) -> Vacancy:
        """Update a vacancy."""
        ...

    def delete_vacancy(self, vacancy_id: str) -> bool:
        """Delete a vacancy by ID."""
        ...

    def delete_vacancy_by_hh_id(self, hh_id: str) -> bool:
        """Delete a vacancy by HH ID."""
        ...

    def vacancy_exists(self, hh_id: str) -> bool:
        """Check if a vacancy exists by HH ID."""
        ...

    def count_vacancies(self) -> int:
        """Count total vacancies."""
        ...
