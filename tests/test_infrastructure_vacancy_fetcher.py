"""Тесты in-memory TTL-кеша для fetcher'а вакансий."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import requests

from hh_applicant_tool.infrastructure.vacancy_fetcher import (
    CachedVacancyDescriptionFetcher,
)


def _build_session_with_html(html: str) -> MagicMock:
    """Мок-сессия, возвращающая заданный HTML на любой GET."""
    response = MagicMock()
    response.text = html
    response.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = response
    return session


def _wrap_vacancy_in_initial_state(vacancy_data: dict) -> str:
    """Оборачивает vacancy_data в формат window.initialState."""
    return f"<html><script>window.initialState = {json.dumps(vacancy_data)};</script></html>"


# ─── Cache miss → fetch → cache ─────────────────────────────────


def test_cache_miss_fetches_from_network():
    """Первый fetch() идёт в сеть и кеширует результат."""
    vacancy = {"id": "1", "name": "Backend", "description": "<p>X</p>"}
    html = _wrap_vacancy_in_initial_state(vacancy)
    session = _build_session_with_html(html)

    fetcher = CachedVacancyDescriptionFetcher(session)
    result = fetcher.fetch("1")

    assert result is not None
    assert result["id"] == "1"
    # Сеть дёрнута один раз
    session.get.assert_called_once()
    # URL формируется с base_url + vacancy_id
    assert session.get.call_args[0][0].endswith("/1")


def test_cache_hit_does_not_call_network():
    """Второй fetch() подряд — данные из кеша, сеть не дёргается."""
    vacancy = {"id": "1", "name": "Backend"}
    html = _wrap_vacancy_in_initial_state(vacancy)
    session = _build_session_with_html(html)

    fetcher = CachedVacancyDescriptionFetcher(session)
    first = fetcher.fetch("1")
    second = fetcher.fetch("1")

    assert first == second
    # Сеть дёрнута лишь раз
    session.get.assert_called_once()


# ─── Cache expired → re-fetch ───────────────────────────────────


def test_cache_expired_refetches(monkeypatch):
    """При истёкшем TTL — кеш сбрасывается и идёт новый запрос."""
    vacancy = {"id": "1", "name": "Backend"}
    html = _wrap_vacancy_in_initial_state(vacancy)
    session = _build_session_with_html(html)

    # ttl=60 секунд
    fetcher = CachedVacancyDescriptionFetcher(session, ttl=60.0)

    # Подменяем time.monotonic, чтобы сэмулировать ход времени
    fake_time = [1000.0]

    monkeypatch.setattr(
        "hh_applicant_tool.infrastructure.vacancy_fetcher.time.monotonic",
        lambda: fake_time[0],
    )

    fetcher.fetch("1")  # cache miss → network
    fake_time[0] += 120.0  # > TTL
    fetcher.fetch("1")  # cache expired → network

    # Сеть дёрнута дважды
    assert session.get.call_count == 2


# ─── Network error ──────────────────────────────────────────────


def test_network_error_returns_none():
    """При HTTPException — fetch() возвращает None, не кеширует."""
    session = MagicMock()
    session.get.side_effect = requests.RequestException("boom")
    fetcher = CachedVacancyDescriptionFetcher(session)

    result = fetcher.fetch("1")
    assert result is None
    # Второй вызов — снова сеть (не кешируется)
    fetcher.fetch("1")
    assert session.get.call_count == 2


def test_http_error_status_raises():
    """raise_for_status() выбрасывает HTTPError — fetch() возвращает None."""
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("500")
    response.text = ""
    session = MagicMock()
    session.get.return_value = response

    fetcher = CachedVacancyDescriptionFetcher(session)
    result = fetcher.fetch("1")
    assert result is None


# ─── HTML без вакансии ──────────────────────────────────────────


def test_unparseable_html_returns_none():
    """HTML без JSON-блока вакансии → fetch() возвращает None."""
    session = _build_session_with_html(
        "<html><body>no vacancy data</body></html>"
    )
    fetcher = CachedVacancyDescriptionFetcher(session)
    assert fetcher.fetch("1") is None


# ─── clear_cache + cache_stats ──────────────────────────────────


def test_clear_cache_resets_state():
    """clear_cache() — после него fetch() снова идёт в сеть."""
    vacancy = {"id": "1", "name": "Backend"}
    html = _wrap_vacancy_in_initial_state(vacancy)
    session = _build_session_with_html(html)

    fetcher = CachedVacancyDescriptionFetcher(session)
    fetcher.fetch("1")
    fetcher.clear_cache()
    fetcher.fetch("1")

    assert session.get.call_count == 2


def test_get_cache_stats_reports_valid_entries():
    """get_cache_stats() считает total/valid/expired и ttl."""
    vacancy = {"id": "1", "name": "X"}
    html = _wrap_vacancy_in_initial_state(vacancy)
    session = _build_session_with_html(html)

    fetcher = CachedVacancyDescriptionFetcher(session, ttl=300.0)
    fetcher.fetch("1")

    stats = fetcher.get_cache_stats()
    assert stats["total"] == 1
    assert stats["valid"] == 1
    assert stats["expired"] == 0
    assert stats["ttl_seconds"] == 300.0


def test_get_cache_stats_counts_expired(monkeypatch):
    """get_cache_stats() корректно считает expired записи."""
    vacancy = {"id": "1", "name": "X"}
    html = _wrap_vacancy_in_initial_state(vacancy)
    session = _build_session_with_html(html)

    fetcher = CachedVacancyDescriptionFetcher(session, ttl=60.0)
    fake_time = [1000.0]

    monkeypatch.setattr(
        "hh_applicant_tool.infrastructure.vacancy_fetcher.time.monotonic",
        lambda: fake_time[0],
    )
    fetcher.fetch("1")
    fake_time[0] += 120.0  # > TTL

    stats = fetcher.get_cache_stats()
    assert stats["total"] == 1
    assert stats["valid"] == 0
    assert stats["expired"] == 1


# ─── Кастомный base_url ─────────────────────────────────────────


def test_custom_base_url_used_in_request():
    """Переданный base_url попадает в URL запроса."""
    vacancy = {"id": "42", "name": "X"}
    html = _wrap_vacancy_in_initial_state(vacancy)
    session = _build_session_with_html(html)

    fetcher = CachedVacancyDescriptionFetcher(
        session,
        base_url="https://custom.example.com/api/vacancies/",
    )
    fetcher.fetch("42")
    url = session.get.call_args[0][0]
    assert url == "https://custom.example.com/api/vacancies/42"


# ─── Разные vacancy_id — разные кеш-записи ─────────────────────


def test_different_ids_cached_separately():
    """Каждый vacancy_id — отдельная запись в кеше."""
    v1 = {"id": "1", "name": "Backend"}
    v2 = {"id": "2", "name": "Frontend"}
    html1 = _wrap_vacancy_in_initial_state(v1)
    html2 = _wrap_vacancy_in_initial_state(v2)
    # Сессия возвращает разный HTML в зависимости от URL
    session = MagicMock()

    def fake_get(url, **kwargs):
        if url.endswith("/1"):
            r = MagicMock()
            r.text = html1
        else:
            r = MagicMock()
            r.text = html2
        r.raise_for_status = MagicMock()
        return r

    session.get.side_effect = fake_get

    fetcher = CachedVacancyDescriptionFetcher(session)
    r1 = fetcher.fetch("1")
    r2 = fetcher.fetch("2")

    assert r1["name"] == "Backend"
    assert r2["name"] == "Frontend"
    assert session.get.call_count == 2
