"""SearchPort -- interface for vacancy search (page iteration + params).

Implemented by :class:`job_bot.application_submit.handlers.search_handler.SearchHandler`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol


class SearchPort(Protocol):
    """Vacancy search (page iteration + params building).

    The handler is the in-slice VSA wrapper around the legacy
    ``_get_vacancies`` / ``_build_search_params`` helpers extracted
    from ``ApplyToVacanciesUseCase`` (issue #145).
    """

    def build_search_params(
        self, command: Any, *, page: int = 0
    ) -> dict[str, Any]:
        """Build the query parameters for the HH ``/vacancies`` or
        ``/resumes/{id}/similar_vacancies`` endpoint.

        ``command`` is duck-typed: reads ``search_params``, ``search``,
        ``order_by``, ``per_page``.
        """
        ...

    def iterate(
        self, command: Any, *, resume_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield raw vacancy dicts by paginating the HH API.

        ``command`` is duck-typed: reads ``per_page``, ``total_pages``,
        ``search``, ``search_params``, ``order_by``.
        """
        ...


__all__ = ["SearchPort"]
