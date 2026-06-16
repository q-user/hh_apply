"""SearchHandler -- vacancy search (page iteration + params).

In-slice VSA wrapper (issue #145) for the legacy
``ApplyToVacanciesUseCase._get_vacancies`` / ``_build_search_params``
helpers. Takes the HH API client via constructor DI; yields raw
vacancy dicts from the ``/vacancies`` (text search) or
``/resumes/{id}/similar_vacancies`` (text-less) endpoints.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, cast

from job_bot.vacancy_search import build_search_params

logger = logging.getLogger(__package__)


class SearchHandler:
    """In-slice search handler (issue #145).

    Mirrors the legacy ``_get_vacancies`` / ``_build_search_params``
    / ``_legacy_vacancy_search`` flow from
    :class:`hh_applicant_tool.application.use_cases.apply_to_vacancies.ApplyToVacanciesUseCase`
    without depending on the legacy package.

    Args:
        api_client: HTTP client for the HH API (with a ``.get`` method).
    """

    def __init__(self, api_client: Any) -> None:
        self._api_client = api_client

    def build_search_params(
        self, command: Any, *, page: int = 0
    ) -> dict[str, Any]:
        """Build the query parameters for the HH search endpoint.

        ``command`` is duck-typed: reads ``search_params`` (flat dict),
        ``search`` (text query), ``order_by`` (sort field), and
        ``per_page`` (results per page).
        """
        sp = dict(command.search_params or {})
        if command.search:
            sp["text"] = command.search
        if command.order_by:
            sp.setdefault("order_by", command.order_by)
        return build_search_params(page=page, per_page=command.per_page, **sp)

    def iterate(
        self, command: Any, *, resume_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield raw vacancy dicts by paginating the HH API.

        When ``command.search`` is set, hits ``/vacancies``; otherwise
        hits ``/resumes/{resume_id}/similar_vacancies`` (requires
        ``resume_id``).
        """
        search_params = self.build_search_params(command, page=0)
        has_text = bool(search_params.get("text"))

        for page in range(command.total_pages):
            logger.debug("Загружаем вакансии со страницы: %d", page + 1)
            params = dict(search_params)
            params["page"] = page
            params["per_page"] = command.per_page

            if has_text:
                endpoint = "/vacancies"
            else:
                if not resume_id:
                    raise ValueError(
                        "resume_id is required for similar_vacancies endpoint"
                    )
                endpoint = f"/resumes/{resume_id}/similar_vacancies"

            res = self._api_client.get(endpoint, params)
            logger.debug("Количество вакансий: %s", res.get("found"))

            items = res.get("items") or []
            if not items:
                return

            yield from cast("list[dict[str, Any]]", items)

            if page >= res.get("pages", 0) - 1:
                return


__all__ = ["SearchHandler"]
