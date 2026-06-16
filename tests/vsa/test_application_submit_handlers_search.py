"""Tests for SearchHandler (issue #145).

The handler wraps the legacy ``_get_vacancies`` /
``_build_search_params`` / ``_legacy_vacancy_search`` helpers
extracted from ``ApplyToVacanciesUseCase``. The tests use a fake
API client that records the last endpoint/params and returns
pre-baked vacancies per page.
"""

from __future__ import annotations

from typing import Any

import pytest

from hh_applicant_tool.application.dto import ApplyToVacanciesCommand
from job_bot.application_submit.handlers.search_handler import SearchHandler


class _FakeApiClient:
    """Fake API client returning a pre-baked ``items`` list per call.

    Args:
        pages: mapping ``page (0-indexed) -> items``. When a page is
            requested that is not in the mapping, an empty ``items``
            list is returned (the handler short-circuits on empty
            pages).
    """

    def __init__(self, pages: dict[int, list[dict[str, Any]]]) -> None:
        self._pages = pages
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((endpoint, params or {}))
        page = (params or {}).get("page", 0)
        items = self._pages.get(page, [])
        return {
            "items": items,
            "found": len(items),
            "pages": max(len(self._pages), 1),
            "page": page,
            "per_page": (params or {}).get("per_page", 100),
        }


# ─── build_search_params ──────────────────────────────────────────────


class TestSearchHandlerBuildSearchParams:
    """``SearchHandler.build_search_params`` combines the command's
    ``search_params`` (flat dict), ``search`` (text query), and
    ``order_by`` into the dict the VSA ``build_search_params``
    expects."""

    def test_text_search_uses_vacancies_endpoint(self) -> None:
        api = _FakeApiClient({0: []})
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            search="python developer", per_page=50, total_pages=1
        )
        params = handler.build_search_params(command, page=0)
        assert params["text"] == "python developer"
        assert params["page"] == 0
        assert params["per_page"] == 50

    def test_search_params_dict_is_merged(self) -> None:
        api = _FakeApiClient({0: []})
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            search_params={"area": ["1"], "schedule": "remote"},
            per_page=20,
            total_pages=1,
        )
        params = handler.build_search_params(command, page=0)
        assert params["area"] == ["1"]
        assert params["schedule"] == "remote"

    def test_order_by_defaults_to_command_when_not_in_search_params(
        self,
    ) -> None:
        api = _FakeApiClient({0: []})
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            order_by="publication_time", per_page=20, total_pages=1
        )
        params = handler.build_search_params(command, page=0)
        assert params["order_by"] == "publication_time"

    def test_order_by_from_search_params_takes_precedence(self) -> None:
        """``search_params['order_by']`` is preserved (the
        ``setdefault`` semantics mirror the legacy behaviour)."""
        api = _FakeApiClient({0: []})
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            search_params={"order_by": "salary_desc"},
            order_by="publication_time",
            per_page=20,
            total_pages=1,
        )
        params = handler.build_search_params(command, page=0)
        assert params["order_by"] == "salary_desc"


# ─── iterate (text search: /vacancies) ───────────────────────────────


class TestSearchHandlerIterateTextSearch:
    """When ``command.search`` is set, the handler hits ``/vacancies``."""

    def test_text_search_yields_vacancies(self) -> None:
        items = [
            {
                "id": 1,
                "name": "Vacancy 1",
                "alternate_url": "https://hh.ru/vacancy/1",
            },
            {
                "id": 2,
                "name": "Vacancy 2",
                "alternate_url": "https://hh.ru/vacancy/2",
            },
        ]
        api = _FakeApiClient({0: items})
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            search="python", per_page=100, total_pages=1
        )
        result = list(handler.iterate(command))
        assert result == items
        assert api.calls[0][0] == "/vacancies"
        assert api.calls[0][1]["text"] == "python"
        assert api.calls[0][1]["page"] == 0

    def test_iterate_paginates_until_pages_exhausted(self) -> None:
        """When ``total_pages > 1`` and the API returns multiple pages,
        iterate() yields all of them."""
        api = _FakeApiClient(
            {
                0: [{"id": 1, "name": "V1"}],
                1: [{"id": 2, "name": "V2"}],
                2: [],  # Empty page → stop iterating
            }
        )
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            search="python", per_page=10, total_pages=5
        )
        result = list(handler.iterate(command))
        assert [r["id"] for r in result] == [1, 2]

    def test_iterate_stops_on_empty_page(self) -> None:
        """Empty ``items`` list short-circuits the loop."""
        api = _FakeApiClient({0: []})
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            search="python", per_page=10, total_pages=5
        )
        result = list(handler.iterate(command))
        assert result == []
        # Only the first page was fetched.
        assert len(api.calls) == 1


# ─── iterate (text-less: /resumes/{id}/similar_vacancies) ─────────────


class TestSearchHandlerIterateSimilarVacancies:
    """When ``command.search`` is empty, the handler hits
    ``/resumes/{id}/similar_vacancies``."""

    def test_similar_vacancies_requires_resume_id(self) -> None:
        api = _FakeApiClient({0: []})
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            search=None, per_page=10, total_pages=1
        )
        with pytest.raises(ValueError, match="resume_id is required"):
            list(handler.iterate(command, resume_id=None))

    def test_similar_vacancies_endpoint_called(self) -> None:
        items = [{"id": 1, "name": "Similar 1"}]
        api = _FakeApiClient({0: items})
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            search=None, per_page=10, total_pages=1
        )
        result = list(handler.iterate(command, resume_id="r1"))
        assert result == items
        assert api.calls[0][0] == "/resumes/r1/similar_vacancies"


# ─── Page bounds ──────────────────────────────────────────────────────


class TestSearchHandlerPageBounds:
    """``iterate()`` stops when ``page >= response['pages'] - 1``."""

    def test_stops_at_last_reported_page(self) -> None:
        """The handler stops after page N when the API reports
        ``pages=2`` (so page 1 is the last)."""
        api = _FakeApiClient(
            {
                0: [{"id": 1, "name": "V1"}],
                1: [{"id": 2, "name": "V2"}],
                2: [{"id": 3, "name": "V3"}],  # should not be reached
            }
        )
        # Force ``pages`` to 2 by wrapping the fake.
        original_get = api.get

        def custom_get(endpoint, params=None):
            res = original_get(endpoint, params)
            res["pages"] = 2
            return res

        api.get = custom_get  # type: ignore[method-assign]
        handler = SearchHandler(api)
        command = ApplyToVacanciesCommand(
            search="python", per_page=10, total_pages=5
        )
        result = list(handler.iterate(command))
        assert [r["id"] for r in result] == [1, 2]


# ─── Protocol satisfaction ────────────────────────────────────────────


def test_search_handler_satisfies_search_port() -> None:
    """The handler structurally satisfies the :class:`SearchPort` protocol."""
    from job_bot.application_submit.ports.search_port import SearchPort

    handler: SearchPort = SearchHandler(_FakeApiClient({0: []}))
    # Check the method names exist (structural typing).
    assert callable(handler.build_search_params)
    assert callable(handler.iterate)
