"""Vacancy search via HH API.

.. versionchanged:: 2.0
   Moved from ``hh_applicant_tool.services.vacancy_search`` to
   ``job_bot.vacancy_search.services.vacancy_search_service``
   as part of the VSA switchover (issue #77).

Extracted from ``operations/apply_vacancies.py`` (issue #3). The service
encapsulates:

- building query parameters for ``/vacancies`` / ``/resumes/{id}/similar_vacancies``;
- pagination and endpoint selection based on ``text`` presence (search)
  or its absence (recommendations).

Dependencies (api_client, per_page, total_pages) are passed explicitly
to the constructor, simplifying unit testing and reuse in
``prepare-vacancies``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from typing import Any

from hh_applicant_tool.api.datatypes import PaginatedItems, SearchVacancy
from hh_applicant_tool.utils.string import bool2str

logger = logging.getLogger(__package__)


def build_search_params(
    *,
    page: int,
    per_page: int,
    order_by: str | None = None,
    text: str | None = None,
    schedule: str | None = None,
    experience: str | None = None,
    currency: str | None = None,
    salary: int | None = None,
    period: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_lat: float | None = None,
    bottom_lat: float | None = None,
    left_lng: float | None = None,
    right_lng: float | None = None,
    sort_point_lat: float | None = None,
    sort_point_lng: float | None = None,
    search_field: list[str] | None = None,
    employment: list[str] | None = None,
    area: list[str] | None = None,
    metro: list[str] | None = None,
    professional_role: list[str] | None = None,
    industry: list[str] | None = None,
    employer_id: list[str] | None = None,
    excluded_employer_id: list[str] | None = None,
    label: list[str] | None = None,
    only_with_salary: bool = False,
    no_magic: bool = False,
    premium: bool = False,
) -> dict[str, Any]:
    """Build query parameters for ``/vacancies`` or ``/resumes/{id}/similar_vacancies``.

    Behaviour mirrors ``Operation._get_search_params`` in
    ``apply_vacancies.py`` (issue #2). Only truthy values go into the
    dict; list values are passed as lists, bool values as
    ``"true"/"false"`` via ``bool2str``.

    Parameters are keyword-only so order doesn't matter and the signature
    is readable when called from ``apply_vacancies.py`` and the future
    ``prepare-vacancies``.
    """
    params: dict[str, Any] = {
        "page": page,
        "per_page": per_page,
    }
    if order_by:
        params["order_by"] = order_by
    if text:
        params["text"] = text
    if schedule:
        params["schedule"] = schedule
    if experience:
        params["experience"] = experience
    if currency:
        params["currency"] = currency
    if salary:
        params["salary"] = salary
    if period:
        params["period"] = period
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if top_lat:
        params["top_lat"] = top_lat
    if bottom_lat:
        params["bottom_lat"] = bottom_lat
    if left_lng:
        params["left_lng"] = left_lng
    if right_lng:
        params["right_lng"] = right_lng
    if sort_point_lat:
        params["sort_point_lat"] = sort_point_lat
    if sort_point_lng:
        params["sort_point_lng"] = sort_point_lng
    if search_field:
        params["search_field"] = list(search_field)
    if employment:
        params["employment"] = list(employment)
    if area:
        params["area"] = list(area)
    if metro:
        params["metro"] = list(metro)
    if professional_role:
        params["professional_role"] = list(professional_role)
    if industry:
        params["industry"] = list(industry)
    if employer_id:
        params["employer_id"] = list(employer_id)
    if excluded_employer_id:
        params["excluded_employer_id"] = list(excluded_employer_id)
    if label:
        params["label"] = list(label)
    if only_with_salary:
        params["only_with_salary"] = bool2str(only_with_salary)
    if no_magic:
        params["no_magic"] = bool2str(no_magic)
    if premium:
        params["premium"] = bool2str(premium)
    return params


class VacancySearchService:
    """Vacancy search service via HH API.

    Attributes:
        api_client: ``ApiClient`` instance (see ``api/client.py``).
        per_page: how many vacancies to request per page.
        total_pages: upper bound on page count (to protect against infinite loops).
    """

    def __init__(self, api_client: Any, *, per_page: int, total_pages: int):
        self.api_client = api_client
        self.per_page = per_page
        self.total_pages = total_pages

    def search(
        self,
        search_params: Mapping[str, Any],
        *,
        resume_id: str | None = None,
    ) -> Iterator[SearchVacancy]:
        """Iterate vacancies across all pages.

        If ``search_params`` contains ``text`` — uses ``/vacancies``
        (keyword search). Otherwise — recommendations
        ``/resumes/{resume_id}/similar_vacancies`` (requires ``resume_id``).

        ``search_params`` is expected to be already formed (via
        :func:`build_search_params` or direct mapping from
        ``SearchProfileModel.search_params``); ``page`` and ``per_page``
        are mixed in by the service.
        """
        has_text = bool(search_params.get("text"))

        for page in range(self.total_pages):
            logger.debug("Загружаем вакансии со страницы: %d", page + 1)
            params = dict(search_params)
            params["page"] = page
            params["per_page"] = self.per_page

            if has_text:
                endpoint = "/vacancies"
            else:
                if not resume_id:
                    raise ValueError(
                        "resume_id is required for similar_vacancies endpoint"
                    )
                endpoint = f"/resumes/{resume_id}/similar_vacancies"

            res: PaginatedItems[SearchVacancy] = self.api_client.get(
                endpoint, params
            )
            logger.debug("Количество вакансий: %s", res.get("found"))

            items = res.get("items") or []
            if not items:
                return

            yield from items

            if page >= res.get("pages", 0) - 1:
                return
