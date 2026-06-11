"""Application Preparation slice - main entry point and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_bot.application_prep.handlers.application_handler import (
    ApplicationHandler,
)
from job_bot.application_prep.handlers.cover_letter_handler import (
    CoverLetterHandler,
)
from job_bot.application_prep.handlers.relevance_handler import (
    RelevanceHandler,
)
from job_bot.application_prep.ports.application_port import ApplicationPort
from job_bot.application_prep.ports.cover_letter_port import CoverLetterPort
from job_bot.application_prep.ports.relevance_port import (
    RelevancePort,
    RelevanceStoragePort,
)
from job_bot.shared.api.client import HHApiClient, HHApiConfig
from job_bot.shared.config.settings import Settings
from job_bot.shared.storage.database import Database, create_database

if TYPE_CHECKING:
    from job_bot.shared.ai.client import AIClient, AIConfig
    from job_bot.vacancy_search.ports.vacancy_port import VacancyPort


class ApplicationPrepSlice:
    """Application Preparation slice - encapsulates all draft preparation functionality.

    Public surface:
    - relevance: AI-based relevance filtering
    - cover_letters: Cover letter generation and persistence
    - applications: Orchestrated draft preparation
    """

    def __init__(
        self,
        database: Database,
        api_client: HHApiClient | None = None,
        ai_client: "AIClient | None" = None,
        *,
        relevance_rules: dict[str, Any] | None = None,
        ai_failure_mode: str = "permissive",
        cover_letter_template: str | None = None,
        vacancy_port: "VacancyPort | None" = None,
    ) -> None:
        self._database = database
        self._api_client = api_client or HHApiClient()
        self._ai_client = ai_client

        # Create handlers
        self._relevance_handler = RelevanceHandler(
            database,
            api_client=self._api_client,
            ai_client=self._ai_client,
            relevance_rules=relevance_rules,
            ai_failure_mode=ai_failure_mode,
        )
        self._cover_letter_handler = CoverLetterHandler(
            database,
            api_client=self._api_client,
            ai_client=self._ai_client,
            template=cover_letter_template,
            vacancy_port=vacancy_port,
        )
        self._application_handler = ApplicationHandler(
            database,
            relevance=self._relevance_handler,
            cover_letter=self._cover_letter_handler,
        )

    @property
    def relevance(self) -> RelevancePort:
        """Get relevance analysis port."""
        return self._relevance_handler

    @property
    def relevance_storage(self) -> RelevanceStoragePort:
        """Get relevance storage port."""
        return self._relevance_handler

    @property
    def cover_letters(self) -> CoverLetterPort:
        """Get cover letter port."""
        return self._cover_letter_handler

    @property
    def applications(self) -> ApplicationPort:
        """Get application draft port."""
        return self._application_handler

    @property
    def database(self) -> Database:
        """Get database instance."""
        return self._database

    @property
    def api_client(self) -> HHApiClient:
        """Get API client instance."""
        return self._api_client


def create_application_prep_slice(
    settings: Settings | None = None,
    database: Database | None = None,
    api_client: HHApiClient | None = None,
    api_config: HHApiConfig | None = None,
    ai_client: "AIClient | None" = None,
    ai_config: "AIConfig | None" = None,
    *,
    relevance_rules: dict[str, Any] | None = None,
    ai_failure_mode: str = "permissive",
    cover_letter_template: str | None = None,
    vacancy_port: "VacancyPort | None" = None,
) -> ApplicationPrepSlice:
    """Factory function to create an ApplicationPrepSlice.

    Args:
        settings: Application settings (optional, will create default if not provided)
        database: Database instance (optional, will create from settings if not provided)
        api_client: HH API client (optional, will create if not provided)
        api_config: HH API config (optional, will use from settings if not provided)
        ai_client: AI client (optional, will create from ai_config if not provided)
        ai_config: AI config (optional, will use from settings if not provided)
        relevance_rules: relevance filtering rules (must_have, nice_to_have, etc.)
        ai_failure_mode: "permissive" | "strict" | "raise" - what to do on AI failure
        cover_letter_template: custom cover letter template (otherwise default)
        vacancy_port: VacancyPort for fetching full vacancy data

    Returns:
        Configured ApplicationPrepSlice instance
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

    if ai_client is None and ai_config is not None:
        from job_bot.shared.ai.client import AIClient

        ai_client = AIClient(config=ai_config)

    return ApplicationPrepSlice(
        database=database,
        api_client=api_client,
        ai_client=ai_client,
        relevance_rules=relevance_rules,
        ai_failure_mode=ai_failure_mode,
        cover_letter_template=cover_letter_template,
        vacancy_port=vacancy_port,
    )
