"""Тесты сервиса поиска вакансий (issue #3)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.services.vacancy_search import (
    VacancySearchService,
    build_search_params,
)

# ─── build_search_params ────────────────────────────────────────────


def test_build_search_params_only_required():
    params = build_search_params(page=0, per_page=100)
    assert params == {"page": 0, "per_page": 100}


def test_build_search_params_includes_truthy():
    params = build_search_params(
        page=0,
        per_page=100,
        order_by="relevance",
        text="Python",
        schedule="remote",
        experience="between3And6",
        salary=250000,
    )
    assert params["order_by"] == "relevance"
    assert params["text"] == "Python"
    assert params["schedule"] == "remote"
    assert params["experience"] == "between3And6"
    assert params["salary"] == 250000


def test_build_search_params_drops_falsy():
    params = build_search_params(
        page=0,
        per_page=100,
        salary=0,
        area=None,
        employment=[],
        only_with_salary=False,
        no_magic=False,
        premium=False,
        date_from="",
        text="",
    )
    # salary=0 (int) is falsy, area=None dropped, employment=[] dropped
    assert "salary" not in params
    assert "area" not in params
    assert "employment" not in params
    assert "only_with_salary" not in params
    assert "no_magic" not in params
    assert "premium" not in params
    assert "date_from" not in params
    assert "text" not in params


def test_build_search_params_bool2str():
    params = build_search_params(
        page=0,
        per_page=100,
        only_with_salary=True,
        no_magic=True,
        premium=True,
    )
    assert params["only_with_salary"] == "true"
    assert params["no_magic"] == "true"
    assert params["premium"] == "true"


def test_build_search_params_list_fields():
    params = build_search_params(
        page=0,
        per_page=100,
        area=["1", "2"],
        employment=["full"],
        professional_role=["96", "165"],
        industry=["7"],
    )
    assert params["area"] == ["1", "2"]
    assert params["employment"] == ["full"]
    assert params["professional_role"] == ["96", "165"]
    assert params["industry"] == ["7"]


def test_build_search_params_geo_fields():
    params = build_search_params(
        page=0,
        per_page=100,
        top_lat=55.8,
        bottom_lat=55.6,
        left_lng=37.3,
        right_lng=37.8,
        sort_point_lat=55.75,
        sort_point_lng=37.62,
    )
    assert params["top_lat"] == 55.8
    assert params["bottom_lat"] == 55.6
    assert params["left_lng"] == 37.3
    assert params["right_lng"] == 37.8
    assert params["sort_point_lat"] == 55.75
    assert params["sort_point_lng"] == 37.62


def test_build_search_params_currency_and_period():
    params = build_search_params(
        page=0,
        per_page=100,
        currency="RUR",
        period=30,
    )
    assert params["currency"] == "RUR"
    assert params["period"] == 30


def test_build_search_params_date_range():
    params = build_search_params(
        page=0,
        per_page=100,
        date_from="2024-01-01",
        date_to="2024-12-31",
    )
    assert params["date_from"] == "2024-01-01"
    assert params["date_to"] == "2024-12-31"


# ─── VacancySearchService.search ────────────────────────────────────


def _make_page(items: list[dict], pages: int) -> dict[str, Any]:
    """Собирает ответ API в формате PaginatedItems."""
    return {"items": items, "found": len(items), "pages": pages, "page": 0}


def test_search_uses_vacancies_endpoint_when_text():
    api = MagicMock()
    api.get.return_value = _make_page([{"id": "v1", "name": "Python"}], 1)
    svc = VacancySearchService(api, per_page=10, total_pages=2)

    results: Iterator[dict] = list(
        svc.search({"text": "Python"}, resume_id="r1")
    )

    assert len(results) == 1
    api.get.assert_called_once()
    endpoint = api.get.call_args[0][0]
    assert endpoint == "/vacancies"


def test_search_uses_similar_vacancies_when_no_text():
    api = MagicMock()
    api.get.return_value = _make_page([{"id": "v1", "name": "Backend"}], 1)
    svc = VacancySearchService(api, per_page=10, total_pages=2)

    results = list(svc.search({}, resume_id="r-abc"))

    assert len(results) == 1
    api.get.assert_called_once()
    endpoint = api.get.call_args[0][0]
    assert endpoint == "/resumes/r-abc/similar_vacancies"


def test_search_requires_resume_id_for_similar():
    api = MagicMock()
    svc = VacancySearchService(api, per_page=10, total_pages=2)

    with pytest.raises(ValueError, match="resume_id is required"):
        list(svc.search({}))


def test_search_paginates_and_stops():
    api = MagicMock()
    api.get.side_effect = [
        _make_page([{"id": "v1"}, {"id": "v2"}], 2),
        _make_page([{"id": "v3"}], 2),
    ]
    svc = VacancySearchService(api, per_page=10, total_pages=3)

    results = list(svc.search({"text": "Python"}, resume_id="r1"))

    assert len(results) == 3
    assert [r["id"] for r in results] == ["v1", "v2", "v3"]
    assert api.get.call_count == 2
    # обе страницы зовут /vacancies
    for call in api.get.call_args_list:
        assert call[0][0] == "/vacancies"


def test_search_stops_on_empty_items():
    api = MagicMock()
    api.get.return_value = _make_page([], 1)
    svc = VacancySearchService(api, per_page=10, total_pages=3)

    results = list(svc.search({"text": "Python"}, resume_id="r1"))

    assert results == []
    assert api.get.call_count == 1


def test_search_stops_when_pages_exhausted_by_api():
    """Если API сказал pages=1, а total_pages=5 — должна быть 1 итерация."""
    api = MagicMock()
    api.get.return_value = _make_page([{"id": "v1"}], 1)
    svc = VacancySearchService(api, per_page=10, total_pages=5)

    results = list(svc.search({"text": "Python"}, resume_id="r1"))

    assert len(results) == 1
    assert api.get.call_count == 1


def test_search_passes_per_page_and_page():
    api = MagicMock()
    api.get.return_value = _make_page([], 1)
    svc = VacancySearchService(api, per_page=50, total_pages=1)

    list(svc.search({"text": "X"}))

    params = api.get.call_args[0][1]
    assert params["per_page"] == 50
    assert params["page"] == 0


def test_search_merges_caller_params():
    """Все ключи из search_params пробрасываются в API-запрос."""
    api = MagicMock()
    api.get.return_value = _make_page([], 1)
    svc = VacancySearchService(api, per_page=10, total_pages=1)

    list(
        svc.search(
            {
                "text": "Go",
                "area": ["1"],
                "salary": 300000,
            }
        )
    )

    params = api.get.call_args[0][1]
    assert params["text"] == "Go"
    assert params["area"] == ["1"]
    assert params["salary"] == 300000
    assert params["page"] == 0
    assert params["per_page"] == 10
