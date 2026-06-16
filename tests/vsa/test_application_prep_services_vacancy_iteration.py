"""Tests for :class:`VacancyIterationService` (issue #147).

Covers the per-phase service that owns the vacancy search loop,
the per-vacancy skip policy, the safe full-vacancy fetch, and the
search/full merge.

Strategy
--------

* **API client** — a small in-process fake (``_FakeApi``) that records
  every ``.get(endpoint, params)`` call and returns canned page
  responses. No ``unittest.mock.Mock`` for in-process test doubles.
* **Storage** — a tiny in-process fake
  (:class:`_FakeStorage`) exposing only the
  ``skipped_vacancies.find(resume_id, vacancy_id)`` method. The
  service is duck-typed on ``storage``; passing a focused fake keeps
  the tests independent of the legacy schema.
* **No external HTTP** — every test exercises the service through
  the fake API and reads the results back from the service.

The tests cover:

* ``search_vacancies`` paginates ``/vacancies`` when ``text`` is set;
* ``search_vacancies`` paginates ``/resumes/{id}/similar_vacancies``
  when ``text`` is absent and ``resume_id`` is provided;
* ``search_vacancies`` raises ``ValueError`` when ``text`` is absent
  and ``resume_id`` is missing;
* ``search_vacancies`` stops on empty page;
* ``search_vacancies`` stops when ``page >= pages - 1``;
* ``skip_reason`` returns ``"already_responded"`` for ``relations``;
* ``skip_reason`` returns ``"archived"`` for ``archived``;
* ``skip_reason`` returns ``"previously_skipped"`` when a row exists;
* ``skip_reason`` returns ``None`` otherwise;
* ``fetch_full_vacancy`` returns ``None`` on falsy id;
* ``fetch_full_vacancy`` returns ``None`` on API error;
* ``merge_vacancy`` prefers ``full_vacancy`` fields;
* ``merge_vacancy`` fills in missing keys from search.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import requests

from job_bot.application_prep.services.vacancy_iteration import (
    VacancyIterationService,
)


class _FakeApi:
    """Tiny in-process HH API fake for vacancy iteration.

    ``responses`` is a ``{endpoint_template: (response, exception)}``
    map. ``_pages`` is a list of (page, items, total_pages) triples
    that simulate paginated responses. Each ``.get`` invocation is
    recorded on ``calls``.
    """

    def __init__(
        self,
        *,
        pages: list[tuple[int, list[dict[str, Any]], int]] | None = None,
        full: dict[str, Any] | None = None,
        raise_on: set[str] | None = None,
    ) -> None:
        self._pages = pages or []
        self._full = full
        self._raise_on = raise_on or set()
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((endpoint, params))
        if endpoint in self._raise_on:
            raise requests.RequestException("api boom")
        if endpoint.startswith("/vacancies/") and not endpoint.startswith(
            "/vacancies?"
        ):
            return self._full or {
                "id": int(endpoint.rsplit("/", 1)[-1]),
                "name": "V",
            }
        # Paginated search endpoint.
        page = (params or {}).get("page", 0)
        for p, items, total in self._pages:
            if p == page:
                return {
                    "items": items,
                    "pages": total,
                    "page": p,
                }
        return {"items": [], "pages": 0, "page": page}


class _FakeSkippedRepo:
    """Tiny in-process ``skipped_vacancies`` fake."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = list(rows or [])

    def find(
        self, *, resume_id: str = "", vacancy_id: Any = None
    ) -> Iterator[dict[str, Any]]:
        for r in self._rows:
            if r.get("resume_id") == resume_id and (
                vacancy_id is None or r.get("vacancy_id") == vacancy_id
            ):
                yield r


class _FakeStorage:
    """Tiny in-process storage fake (only ``skipped_vacancies``)."""

    def __init__(self, skipped: list[dict[str, Any]] | None = None) -> None:
        self.skipped_vacancies = _FakeSkippedRepo(skipped)


# ─── search_vacancies ─────────────────────────────────────────────


class TestSearchVacancies:
    """``search_vacancies`` paginates the right endpoint and stops cleanly."""

    def test_text_search_paginates_vacancies_endpoint(self) -> None:
        """``text`` in ``search_params`` → ``GET /vacancies``."""
        api = _FakeApi(
            pages=[
                (0, [{"id": 1}, {"id": 2}], 2),
                (1, [{"id": 3}], 2),
            ]
        )
        service = VacancyIterationService(api_client=api)

        results = list(
            service.search_vacancies(
                {"text": "python"},
                per_page=2,
                total_pages=5,
                resume_id="r1",
            )
        )

        assert [v["id"] for v in results] == [1, 2, 3]
        # Every call went to /vacancies.
        assert all(ep == "/vacancies" for ep, _ in api.calls)
        # Pages were requested in order with the right params.
        assert [c[1]["page"] for c in api.calls] == [0, 1]
        assert all(c[1]["per_page"] == 2 for c in api.calls)

    def test_similar_vacancies_uses_resume_endpoint(self) -> None:
        """No ``text`` → ``GET /resumes/{id}/similar_vacancies``."""
        api = _FakeApi(
            pages=[
                (0, [{"id": 10}, {"id": 11}], 1),
            ]
        )
        service = VacancyIterationService(api_client=api)

        results = list(
            service.search_vacancies(
                {},
                per_page=100,
                total_pages=5,
                resume_id="r1",
            )
        )

        assert [v["id"] for v in results] == [10, 11]
        assert api.calls[0][0] == "/resumes/r1/similar_vacancies"

    def test_missing_resume_id_for_similar_raises(self) -> None:
        """No ``text`` and no ``resume_id`` → ``ValueError`` (the
        similar_vacancies endpoint is per-resume)."""
        service = VacancyIterationService(api_client=_FakeApi())

        with pytest.raises(ValueError, match="resume_id is required"):
            list(
                service.search_vacancies(
                    {},
                    per_page=100,
                    total_pages=5,
                    resume_id=None,
                )
            )

    def test_stops_on_empty_page(self) -> None:
        """Empty ``items`` list short-circuits the loop (the empty
        page itself was already requested)."""
        api = _FakeApi(
            pages=[
                (0, [{"id": 1}], 5),
                (1, [], 5),
            ]
        )
        service = VacancyIterationService(api_client=api)

        results = list(
            service.search_vacancies(
                {"text": "x"},
                per_page=10,
                total_pages=5,
                resume_id="r1",
            )
        )

        assert [v["id"] for v in results] == [1]
        # The empty page is requested but the loop exits before
        # requesting page 2.
        assert len(api.calls) == 2
        assert [c[1]["page"] for c in api.calls] == [0, 1]

    def test_stops_on_last_page_marker(self) -> None:
        """``page >= pages - 1`` stops the loop (HH sends total pages
        including the current one)."""
        api = _FakeApi(
            pages=[
                (0, [{"id": 1}], 1),
            ]
        )
        service = VacancyIterationService(api_client=api)

        results = list(
            service.search_vacancies(
                {"text": "x"},
                per_page=10,
                total_pages=5,
                resume_id="r1",
            )
        )

        assert [v["id"] for v in results] == [1]
        assert len(api.calls) == 1


# ─── skip_reason ───────────────────────────────────────────────────


class TestSkipReason:
    """``skip_reason`` returns a reason string or ``None``."""

    def test_relations_returns_already_responded(self) -> None:
        service = VacancyIterationService(
            api_client=_FakeApi(), storage=_FakeStorage()
        )
        assert (
            service.skip_reason({"id": 1, "relations": ["got_response"]}, "r1")
            == "already_responded"
        )

    def test_archived_returns_archived(self) -> None:
        service = VacancyIterationService(
            api_client=_FakeApi(), storage=_FakeStorage()
        )
        assert (
            service.skip_reason({"id": 1, "archived": True}, "r1") == "archived"
        )

    def test_previously_skipped_per_resume(self) -> None:
        storage = _FakeStorage(
            skipped=[
                {"resume_id": "r1", "vacancy_id": 1, "reason": "ai_rejected"}
            ]
        )
        service = VacancyIterationService(
            api_client=_FakeApi(), storage=storage
        )
        assert service.skip_reason({"id": 1}, "r1") == "previously_skipped"

    def test_previously_skipped_global(self) -> None:
        """``resume_id=""`` row is a global skip; matches regardless of
        the active resume."""
        storage = _FakeStorage(
            skipped=[{"resume_id": "", "vacancy_id": 1, "reason": "x"}]
        )
        service = VacancyIterationService(
            api_client=_FakeApi(), storage=storage
        )
        assert service.skip_reason({"id": 1}, "r-other") == "previously_skipped"

    def test_no_skip_returns_none(self) -> None:
        service = VacancyIterationService(
            api_client=_FakeApi(), storage=_FakeStorage()
        )
        assert service.skip_reason({"id": 1}, "r1") is None

    def test_no_storage_disables_skip(self) -> None:
        """``storage=None`` short-circuits the previously_skipped check
        (useful for ad-hoc callers that don't have a facade)."""
        service = VacancyIterationService(api_client=_FakeApi(), storage=None)
        assert service.skip_reason({"id": 1}, "r1") is None

    def test_repository_error_does_not_propagate(self) -> None:
        """A ``RepositoryError`` from the storage is swallowed and the
        vacancy is processed (the legacy contract: never fail the run
        on a skip-list read)."""

        class _BrokenStorage:
            @property
            def skipped_vacancies(self) -> Any:
                class _Repo:
                    def find(self, **kwargs: Any) -> Iterator[Any]:
                        from hh_applicant_tool.storage.repositories.errors import (
                            RepositoryError,
                        )

                        raise RepositoryError("boom")
                        yield  # pragma: no cover  # noqa: F841

                return _Repo()

        service = VacancyIterationService(
            api_client=_FakeApi(), storage=_BrokenStorage()
        )
        assert service.skip_reason({"id": 1}, "r1") is None


# ─── fetch_full_vacancy ────────────────────────────────────────────


class TestFetchFullVacancy:
    """``fetch_full_vacancy`` is the safe full-vacancy fetch."""

    def test_returns_dict_on_success(self) -> None:
        api = _FakeApi(full={"id": 1, "name": "Senior", "description": "..."})
        service = VacancyIterationService(api_client=api)

        full = service.fetch_full_vacancy(1)

        assert full == {"id": 1, "name": "Senior", "description": "..."}
        assert api.calls[0][0] == "/vacancies/1"

    def test_returns_none_on_falsy_id(self) -> None:
        api = _FakeApi()
        service = VacancyIterationService(api_client=api)

        assert service.fetch_full_vacancy(None) is None
        assert service.fetch_full_vacancy(0) is None
        assert service.fetch_full_vacancy("") is None
        # No API call was made.
        assert api.calls == []

    def test_returns_none_on_request_exception(self) -> None:
        api = _FakeApi(raise_on={"/vacancies/1"})
        service = VacancyIterationService(api_client=api)

        assert service.fetch_full_vacancy(1) is None


# ─── merge_vacancy ─────────────────────────────────────────────────


class TestMergeVacancy:
    """``merge_vacancy`` prefers ``full`` but fills in response-state
    keys from the search result."""

    def test_no_full_returns_search(self) -> None:
        service = VacancyIterationService(api_client=_FakeApi())
        search = {"id": 1, "name": "X", "relations": ["got_response"]}
        assert service.merge_vacancy(search, None) == search

    def test_full_wins_for_known_fields(self) -> None:
        service = VacancyIterationService(api_client=_FakeApi())
        full = {"id": 1, "name": "Full Name", "description": "..."}
        search = {"id": 1, "name": "Search Name"}
        merged = service.merge_vacancy(search, full)
        assert merged["name"] == "Full Name"
        assert merged["description"] == "..."

    def test_search_fills_missing_response_state_keys(self) -> None:
        """The search result carries ``relations`` / ``has_test`` /
        ``response_url`` / etc. that the full payload doesn't have;
        the service copies them across so the per-vacancy pipeline
        sees both the rich description and the response state."""
        service = VacancyIterationService(api_client=_FakeApi())
        full = {
            "id": 1,
            "name": "Full",
            "description": "...",
            "relations": [],  # explicitly empty
        }
        search = {
            "id": 1,
            "name": "Search",
            "relations": ["got_response"],
            "has_test": True,
            "response_url": "https://hh.ru/apply/1",
            "alternate_url": "https://hh.ru/vacancy/1",
        }
        merged = service.merge_vacancy(search, full)
        # ``full`` has ``relations=[]`` so the copy is suppressed.
        assert merged["relations"] == []
        # The other response-state keys are missing from ``full``,
        # so they're copied from the search result.
        assert merged["has_test"] is True
        assert merged["response_url"] == "https://hh.ru/apply/1"
        assert merged["alternate_url"] == "https://hh.ru/vacancy/1"
        # ``full``'s description wins.
        assert merged["description"] == "..."

    def test_does_not_overwrite_full_with_search(self) -> None:
        """When ``full`` already has a response-state key, the search
        value must NOT overwrite it."""
        service = VacancyIterationService(api_client=_FakeApi())
        full = {
            "id": 1,
            "name": "Full",
            "response_url": "https://hh.ru/full",
        }
        search = {
            "id": 1,
            "response_url": "https://hh.ru/search",
        }
        merged = service.merge_vacancy(search, full)
        assert merged["response_url"] == "https://hh.ru/full"


# ─── Constructor defaults ──────────────────────────────────────────


class TestConstructorDefaults:
    """``__init__`` accepts ``storage=None`` and ``progress_callback=None``."""

    def test_works_with_no_storage(self) -> None:
        service = VacancyIterationService(api_client=_FakeApi())
        assert service.storage is None
        assert service.progress_callback is None

    def test_records_progress_callback(self) -> None:
        service = VacancyIterationService(
            api_client=_FakeApi(), progress_callback=lambda m: None
        )
        assert service.progress_callback is not None
