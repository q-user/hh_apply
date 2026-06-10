"""Port for vacancy search operations - main interface for other slices."""

from __future__ import annotations

from typing import Any, Protocol

from job_bot.vacancy_search.models.search_profile import SearchProfile
from job_bot.vacancy_search.models.vacancy import Vacancy


class VacancySearchPort(Protocol):
    """Port for vacancy search operations.

    This is the main interface that other slices (like application_prep)
    will use to search for vacancies using search profiles.
    """

    def search_vacancies(
        self,
        profile: SearchProfile,
        access_token: str,
        max_pages: int = 1,
    ) -> list[Vacancy]:
        """Search vacancies using a search profile.

        Args:
            profile: Search profile with criteria
            access_token: HH.ru OAuth access token
            max_pages: Maximum number of pages to fetch

        Returns:
            List of vacancies found
        """
        ...

    def search_vacancies_raw(
        self,
        params: dict[str, Any],
        access_token: str,
        max_pages: int = 1,
    ) -> list[Vacancy]:
        """Search vacancies using raw API parameters.

        Args:
            params: Raw HH API search parameters
            access_token: HH.ru OAuth access token
            max_pages: Maximum number of pages to fetch

        Returns:
            List of vacancies found
        """
        ...

    def fetch_vacancy_details(
        self,
        vacancy_id: str,
        access_token: str,
    ) -> Vacancy | None:
        """Fetch full vacancy details by ID.

        Args:
            vacancy_id: HH vacancy ID
            access_token: HH.ru OAuth access token

        Returns:
            Vacancy with full details or None if not found
        """
        ...
