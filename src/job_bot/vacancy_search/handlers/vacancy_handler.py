"""Vacancy handler - business logic for vacancies."""

from __future__ import annotations

from job_bot.shared.storage.database import Database
from job_bot.vacancy_search.models.vacancy import Vacancy, VacancyCreate
from job_bot.vacancy_search.repositories.vacancy_repo import VacancyRepository


class VacancyHandler:
    """Handler for vacancy operations."""

    def __init__(self, database: Database) -> None:
        self._repo = VacancyRepository(database)

    # Implementation of VacancyPort

    def create_vacancy(self, vacancy: VacancyCreate) -> Vacancy:
        """Create a new vacancy record."""
        entity = vacancy.to_vacancy()
        return self._repo.create(entity)

    def get_vacancy(self, vacancy_id: str) -> Vacancy | None:
        """Get a vacancy by ID."""
        return self._repo.get_by_id(vacancy_id)

    def get_vacancy_by_hh_id(self, hh_id: str) -> Vacancy | None:
        """Get a vacancy by HH ID."""
        return self._repo.get_by_hh_id(hh_id)

    def list_vacancies(
        self, limit: int = 100, offset: int = 0
    ) -> list[Vacancy]:
        """List vacancies with pagination."""
        return self._repo.get_all(limit=limit, offset=offset)

    def search_vacancies(
        self,
        keywords: str | None = None,
        employer_name: str | None = None,
        area_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Vacancy]:
        """Search vacancies with filters."""
        return self._repo.search(
            keywords=keywords,
            employer_name=employer_name,
            area_name=area_name,
            limit=limit,
            offset=offset,
        )

    def update_vacancy(self, vacancy: Vacancy) -> Vacancy:
        """Update a vacancy."""
        return self._repo.update(vacancy)

    def delete_vacancy(self, vacancy_id: str) -> bool:
        """Delete a vacancy by ID."""
        return self._repo.delete(vacancy_id)

    def delete_vacancy_by_hh_id(self, hh_id: str) -> bool:
        """Delete a vacancy by HH ID."""
        return self._repo.delete_by_hh_id(hh_id)

    def vacancy_exists(self, hh_id: str) -> bool:
        """Check if a vacancy exists by HH ID."""
        return self._repo.exists_by_hh_id(hh_id)

    def count_vacancies(self) -> int:
        """Count total vacancies."""
        return self._repo.count()
