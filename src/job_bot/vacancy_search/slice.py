"""Vacancy Search slice - main entry point and factory."""

from __future__ import annotations

from job_bot.shared.api.client import HHApiClient, HHApiConfig
from job_bot.shared.config.settings import Settings
from job_bot.shared.storage.database import Database, create_database
from job_bot.vacancy_search.handlers.search_profile_handler import (
    SearchProfileHandler,
)
from job_bot.vacancy_search.handlers.vacancy_handler import VacancyHandler
from job_bot.vacancy_search.handlers.vacancy_search_handler import (
    VacancySearchHandler,
)
from job_bot.vacancy_search.ports.search_profile_port import SearchProfilePort
from job_bot.vacancy_search.ports.vacancy_port import VacancyPort
from job_bot.vacancy_search.ports.vacancy_search_port import VacancySearchPort


class VacancySearchSlice:
    """Vacancy Search slice - encapsulates all vacancy search functionality."""

    def __init__(
        self,
        database: Database,
        api_client: HHApiClient | None = None,
        api_config: HHApiConfig | None = None,
    ) -> None:
        self._database = database
        self._api_client = api_client or HHApiClient(config=api_config)

        # Create handlers
        self._search_profile_handler = SearchProfileHandler(database)
        self._vacancy_handler = VacancyHandler(database)
        self._vacancy_search_handler = VacancySearchHandler(
            database, api_client=self._api_client, api_config=api_config
        )

    @property
    def search_profiles(self) -> SearchProfilePort:
        """Get search profile port."""
        return self._search_profile_handler

    @property
    def vacancies(self) -> VacancyPort:
        """Get vacancy port."""
        return self._vacancy_handler

    @property
    def search(self) -> VacancySearchPort:
        """Get vacancy search port."""
        return self._vacancy_search_handler

    @property
    def database(self) -> Database:
        """Get database instance."""
        return self._database

    @property
    def api_client(self) -> HHApiClient:
        """Get API client instance."""
        return self._api_client


def create_vacancy_search_slice(
    settings: Settings | None = None,
    database: Database | None = None,
    api_client: HHApiClient | None = None,
    api_config: HHApiConfig | None = None,
) -> VacancySearchSlice:
    """Factory function to create a VacancySearchSlice.

    Args:
        settings: Application settings (optional, will create default if not provided)
        database: Database instance (optional, will create from settings if not provided)
        api_client: HH API client (optional, will create if not provided)
        api_config: HH API config (optional, will use from settings if not provided)

    Returns:
        Configured VacancySearchSlice instance
    """
    if settings is None:
        from job_bot.shared.config.settings import load_settings

        settings = load_settings()

    if database is None:
        database = create_database(settings.database.path)

    if api_config is None:
        api_config = HHApiConfig(
            base_url=settings.hh_api.base_url,
            user_agent=settings.hh_api.user_agent,
            timeout=settings.hh_api.timeout,
        )

    if api_client is None:
        api_client = HHApiClient(config=api_config)

    return VacancySearchSlice(
        database=database,
        api_client=api_client,
        api_config=api_config,
    )
