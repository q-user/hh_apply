"""Shared mock helpers for the integration tests (issue #63).

This module is **not** a conftest — it's a plain helper module
importable by the test files. Splitting these out of ``conftest.py``
avoids the "``from .conftest import …``" pitfall (conftest is not
guaranteed to be importable from inside a test file) and gives the
test files a single, well-typed place to reach for shared doubles.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any
from unittest.mock import MagicMock


# ─── Mock HH API ───────────────────────────────────────────────────────


class MockHHApiResponse:
    """``requests.Response``-shaped double returned by ``MockHHApiClient``."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = json.dumps(payload)
        self.request = MagicMock()

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    # ─── dict-like access ─────────────────────────────────────────────
    # Production handlers in VSA slices call .get() on the response
    # object, treating it like a parsed dict (see issue #102). These
    # methods make the mock match that contract without forcing every
    # call site to do response.json().get(...).

    def get(self, key: str, default: Any = None) -> Any:
        if isinstance(self._payload, dict):
            return self._payload.get(key, default)
        return default

    def __contains__(self, key: object) -> bool:
        if isinstance(self._payload, dict):
            return key in self._payload
        return False

    def __getitem__(self, key: str) -> Any:
        if isinstance(self._payload, dict):
            return self._payload[key]
        raise KeyError(key)


class MockHHApiClient:
    """Deterministic stand-in for ``HHApiClient`` used in integration tests.

    Routes GET / POST calls against a small scenario table; tests can
    populate ``scripted_responses`` / ``negotiation_responses`` to drive
    the workflow under test. No real network IO is performed.
    """

    def __init__(self) -> None:
        self.scripted_responses: dict[tuple[str, str], list[Any]] = {}
        self.negotiation_responses: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.access_token: str | None = "test-access-token"
        self.refresh_token: str | None = "test-refresh-token"
        self.access_expires_at: int = 0
        self._set_access_token_calls: list[str] = []

    def set_access_token(self, token: str) -> None:
        self._set_access_token_calls.append(token)
        self.access_token = token

    def set_refresh_token(self, token: str) -> None:
        self.refresh_token = token

    def _route(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> MockHHApiResponse:
        self.calls.append((method, endpoint, params))
        key = (method, endpoint)
        # POST /negotiations drains ``negotiation_responses`` in order
        if method == "POST" and endpoint == "/negotiations":
            if self.negotiation_responses:
                payload = self.negotiation_responses.pop(0)
                return MockHHApiResponse(payload, status_code=201)
            return MockHHApiResponse(
                {"id": "neg-default", "state": {"name": "response"}},
                status_code=201,
            )
        # Generic scripted lookup
        if key in self.scripted_responses and self.scripted_responses[key]:
            return MockHHApiResponse(self.scripted_responses[key].pop(0))
        # Vacancy detail endpoint
        if method == "GET" and endpoint.startswith("/vacancies/"):
            vid = endpoint.rsplit("/", 1)[-1]
            return MockHHApiResponse(
                {
                    "id": vid,
                    "name": f"Mock Vacancy {vid}",
                    "description": "Mock description",
                    "alternate_url": f"https://hh.ru/vacancy/{vid}",
                }
            )
        # Vacancy search
        if method == "GET" and endpoint == "/vacancies":
            return MockHHApiResponse(
                {
                    "items": [],
                    "pages": 0,
                    "page": params.get("page", 0) if params else 0,
                }
            )
        if method == "GET" and endpoint.startswith("/resumes/"):
            return MockHHApiResponse(
                {
                    "id": endpoint.rsplit("/", 1)[-1],
                    "title": "Backend Developer",
                    "skill_set": ["Python", "Django", "FastAPI"],
                    "experience": [
                        {
                            "company": "MockCo",
                            "position": "Senior",
                            "start": "2020-01",
                            "end": None,
                            "description": "Built things",
                        }
                    ],
                }
            )
        if method == "GET" and endpoint == "/resumes/mine":
            return MockHHApiResponse({"items": []})
        return MockHHApiResponse({})

    def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> MockHHApiResponse:
        return self._route("GET", endpoint, params=params)

    def post(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> MockHHApiResponse:
        return self._route("POST", endpoint, params=json_data or data)

    def put(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
    ) -> MockHHApiResponse:
        return self._route("PUT", endpoint, params=json_data)

    def delete(self, endpoint: str) -> MockHHApiResponse:
        return self._route("DELETE", endpoint)


def default_resumes_payload() -> dict[str, Any]:
    """3 published resumes for the default ``/resumes/mine`` fixture."""
    return {
        "items": [
            {
                "id": "r1",
                "title": "Senior Python Developer",
                "status": {"id": "published"},
                "skill_set": ["Python", "Django", "FastAPI", "PostgreSQL"],
                "alternate_url": "https://hh.ru/resume/r1",
            },
            {
                "id": "r2",
                "title": "Backend Engineer",
                "status": {"id": "published"},
                "skill_set": ["Python", "Flask", "Docker"],
                "alternate_url": "https://hh.ru/resume/r2",
            },
            {
                "id": "r3",
                "title": "DevOps",
                "status": {"id": "not_published"},
                "skill_set": ["K8s", "Terraform"],
            },
        ]
    }


def default_vacancies_payload() -> dict[str, Any]:
    """5 vacancies on the first search page (the default scenario)."""
    return {
        "items": [
            {
                "id": str(100 + i),
                "name": f"Vacancy {100 + i}",
                "employer": {"id": 900 + i, "name": f"Employer {i}"},
                "salary": {"from": 200000, "to": 300000, "currency": "RUR"},
                "area": {"name": "Москва"},
                "alternate_url": f"https://hh.ru/vacancy/{100 + i}",
                "has_test": False,
                "response_letter_required": True,
            }
            for i in range(5)
        ],
        "pages": 1,
        "page": 0,
    }


# ─── Mock transports ──────────────────────────────────────────────────


class MockTelegramTransport:
    """Stub for :class:`TelegramTransportPort`.

    Records every ``send_message`` call and lets tests script a queue
    of updates to feed into :class:`BotService.dispatch_update`.
    """

    def __init__(self, allowed_user_ids: tuple[int, ...] = (1,)) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.sent_digests: list[dict[str, Any]] = []
        self._scripted_updates: list[dict[str, Any]] = []
        self._get_updates_calls: int = 0
        self.allowed_user_ids = allowed_user_ids
        self.poll_timeout = 30

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        msg = {"chat_id": chat_id, "text": text}
        self.sent_messages.append(msg)
        return {"ok": True, "message_id": len(self.sent_messages)}

    def get_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        self._get_updates_calls += 1
        if self._scripted_updates:
            return [self._scripted_updates.pop(0)]
        return []

    def send_digest(self, chat_id: int, text: str) -> dict[str, Any]:
        msg = {"chat_id": chat_id, "text": text}
        self.sent_digests.append(msg)
        return {"ok": True}

    def script_update(self, update: dict[str, Any]) -> None:
        """Queue an update to be returned by the next ``get_updates`` call."""
        self._scripted_updates.append(update)


class MockMaxTransport:
    """Stub for :class:`MaxTransportPort`."""

    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []
        self._scripted_updates: list[dict[str, Any]] = []
        self.allowed_user_ids: tuple[int, ...] = ()

    def send_message(self, chat_id: int, text: str) -> bool:
        self.sent_messages.append((chat_id, text))
        return True

    def get_updates(
        self, offset: int | None = None, timeout: int = 30
    ) -> list[dict[str, Any]]:
        if self._scripted_updates:
            return [self._scripted_updates.pop(0)]
        return []

    def script_update(self, update: dict[str, Any]) -> None:
        self._scripted_updates.append(update)


# ─── Deterministic AI client ───────────────────────────────────────────


class DeterministicAIClient:
    """AI client stub that returns a deterministic response per prompt.

    The response is keyed by ``prompt + system_prompt`` so tests can
    assert that the *same* prompt maps to the *same* response across
    slices (proving no stale caching).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.mode: str = "deterministic"
        self._cache: dict[str, str] = {}

    def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        key = prompt + "|" + (system_prompt or "")
        self.calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "temperature": temperature,
            }
        )
        if key not in self._cache:
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
            if self.mode == "suitable":
                self._cache[key] = (
                    '{"suitable": true, "score": 85, '
                    '"reason": "deterministic match"}'
                )
            elif self.mode == "unsuitable":
                self._cache[key] = (
                    '{"suitable": false, "score": 20, '
                    '"reason": "deterministic mismatch"}'
                )
            elif self.mode == "letter":
                self._cache[key] = (
                    f"Здравствуйте!\n\nЭто сопроводительное письмо "
                    f"[hash={digest}] для вакансии.\n\nС уважением."
                )
            else:
                self._cache[key] = f"AI:{digest}"
        return self._cache[key]


# ─── Misc helpers ──────────────────────────────────────────────────────


class NoOpDelay:
    """No-op stand-in for ``infrastructure.delay.Delay``.

    Using a tiny class (not ``MagicMock``) keeps ``ruff`` and mypy
    happy with the typed ``WorkerService(delay=...)`` constructor.
    """

    def sleep(self, seconds: float) -> None:
        return None


def open_test_connection(db_path) -> sqlite3.Connection:
    """Open a fresh ``sqlite3.Connection`` with the canonical schema."""
    from hh_applicant_tool.storage import StorageFacade

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    StorageFacade(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
