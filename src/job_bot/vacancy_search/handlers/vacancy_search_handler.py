"""Vacancy Search handler - business logic for searching vacancies via HH API."""

from __future__ import annotations

import logging
from typing import Any

import requests
from hh_applicant_tool.api import BadResponse
from hh_applicant_tool.api.errors import ApiError

from job_bot.shared.api.client import HHApiClient, HHApiConfig
from job_bot.shared.storage.database import Database
from job_bot.vacancy_search.handlers.vacancy_handler import VacancyHandler
from job_bot.vacancy_search.models.search_profile import SearchProfile
from job_bot.vacancy_search.models.vacancy import Vacancy, VacancyCreate

logger = logging.getLogger(__package__)


class VacancySearchHandler:
    """Handler for vacancy search operations via HH API."""

    def __init__(
        self,
        database: Database,
        api_client: HHApiClient | None = None,
        api_config: HHApiConfig | None = None,
    ) -> None:
        self._vacancy_handler = VacancyHandler(database)
        self._api_client = api_client or HHApiClient(config=api_config)

    def set_access_token(self, token: str) -> None:
        """Set the access token for API requests."""
        self._api_client.set_access_token(token)

    # Implementation of VacancySearchPort

    def search_vacancies(
        self,
        profile: SearchProfile,
        access_token: str,
        max_pages: int = 1,
    ) -> list[Vacancy]:
        """Search vacancies using a search profile."""
        self.set_access_token(access_token)
        params = profile.to_api_params()
        return self.search_vacancies_raw(params, access_token, max_pages)

    def search_vacancies_raw(
        self,
        params: dict[str, Any],
        access_token: str,
        max_pages: int = 1,
    ) -> list[Vacancy]:
        """Search vacancies using raw API parameters."""
        self.set_access_token(access_token)

        all_vacancies: list[Vacancy] = []

        for page in range(max_pages):
            params["page"] = page
            try:
                response = self._api_client.get("/vacancies", params=params)
                items = response.get("items", [])

                if not items:
                    break

                for item in items:
                    vacancy = Vacancy.from_hh_api(item)
                    # Save to local storage
                    if not self._vacancy_handler.vacancy_exists(vacancy.hh_id):
                        create_data = VacancyCreate(
                            hh_id=vacancy.hh_id,
                            name=vacancy.name,
                            employer_name=vacancy.employer_name,
                            employer_id=vacancy.employer_id,
                            area_name=vacancy.area_name,
                            salary_from=vacancy.salary_from,
                            salary_to=vacancy.salary_to,
                            currency=vacancy.currency,
                            experience=vacancy.experience,
                            employment=vacancy.employment,
                            schedule=vacancy.schedule,
                            description=vacancy.description,
                            key_skills=vacancy.key_skills,
                            published_at=vacancy.published_at,
                            raw_data=vacancy.raw_data,
                        )
                        self._vacancy_handler.create_vacancy(create_data)
                    all_vacancies.append(vacancy)

                # Check if there are more pages
                if page >= response.get("pages", 1) - 1:
                    break

            except (requests.RequestException, ApiError, BadResponse, ValueError, KeyError, TypeError) as ex:
                # Log error and continue
                logger.debug("vacancy search page failed: %s", ex)
                break

        return all_vacancies

    def fetch_vacancy_details(
        self,
        vacancy_id: str,
        access_token: str,
    ) -> Vacancy | None:
        """Fetch full vacancy details by ID."""
        self.set_access_token(access_token)

        try:
            response = self._api_client.get(f"/vacancies/{vacancy_id}")
            return Vacancy.from_hh_api(response)
        except (requests.RequestException, ApiError, BadResponse, ValueError, KeyError, TypeError) as ex:
            logger.debug("fetch_vacancy_details(%s) failed: %s", vacancy_id, ex)
            return None
