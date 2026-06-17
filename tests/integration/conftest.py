"""Shared fixtures for VSA integration tests (issue #63).

The fixtures here compose multiple slices in a single test:
  * ephemeral in-memory SQLite with the canonical schema,
  * mocked HH API client (deterministic, no real network),
  * mocked Telegram / MAX transports (record & replay),
  * deterministic AI client (response keyed by prompt hash),
  * slice composition root for cross-slice workflows.

All doubles live in :mod:`tests.integration._mocks` (not in this
conftest) so the test files can import them by name without the
``from .conftest import …`` pitfall.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration._mocks import (
    DeterministicAIClient,
    MockHHApiClient,
    MockMaxTransport,
    MockTelegramTransport,
    default_resumes_payload,
    default_vacancies_payload,
    open_test_connection,
)


# ─── DB fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    """Fresh on-disk SQLite path for the test.

    On-disk (not ``:memory:``) so the same path can be re-opened by
    multiple slices in the same test without connection-scope surprises.
    """
    return tmp_path / "integration_test.db"


@pytest.fixture
def test_db(test_db_path: Path) -> Iterator[sqlite3.Connection]:
    """Single shared ``sqlite3.Connection`` with the canonical schema.

    The telegram / max slices open their own connections; the
    submit slice takes the raw connection directly. The application
    prep / vacancy_search / config_auth slices all wrap a
    :class:`Database` around this same path so they share state.
    """
    conn = open_test_connection(test_db_path)
    try:
        yield conn
    finally:
        conn.close()
        try:
            test_db_path.unlink(missing_ok=True)
        except OSError:
            pass


# ─── Mock HH API ───────────────────────────────────────────────────────


@pytest.fixture
def mock_hh_api() -> MockHHApiClient:
    """Return a :class:`MockHHApiClient` populated with realistic defaults.

    Default scenario: 3 published resumes, 5 vacancies on the first
    search page, and a successful POST /negotiations.
    """
    client = MockHHApiClient()
    client.scripted_responses[("GET", "/resumes/mine")] = [
        default_resumes_payload()
    ]
    client.scripted_responses[("GET", "/vacancies")] = [
        default_vacancies_payload()
    ]
    return client


# ─── Mock transports ──────────────────────────────────────────────────


@pytest.fixture
def mock_telegram_transport() -> MockTelegramTransport:
    return MockTelegramTransport(allowed_user_ids=(1,))


@pytest.fixture
def mock_max_transport() -> MockMaxTransport:
    return MockMaxTransport()


# ─── Deterministic AI client ───────────────────────────────────────────


@pytest.fixture
def mock_ai_client() -> DeterministicAIClient:
    return DeterministicAIClient()


# ─── Composed slice wiring ────────────────────────────────────────────


@pytest.fixture
def slices(
    test_db: sqlite3.Connection,
    test_db_path: Path,
    mock_hh_api: MockHHApiClient,
    mock_telegram_transport: MockTelegramTransport,
    mock_max_transport: MockMaxTransport,
    mock_ai_client: DeterministicAIClient,
) -> SimpleNamespace:
    """Build the seven slices used by the integration tests.

    Mirrors the wiring :class:`AppContainer` performs in production
    (issues #56, #57, #58, #59), but driven by the test DB and
    mocks instead of the real ``HHApplicantTool``.

    Note (issue raised by code review): the telegram bot slice opens
    its own long-lived ``sqlite3.Connection`` via ``_resolve_storage``
    (documented behaviour of the slice). To avoid two writers fighting
    over the same on-disk SQLite file, the telegram slice is wired
    against a *fresh* ``:memory:`` connection that is closed by the
    ``slices`` fixture teardown. All other slices share the
    ``test_db`` connection (or a ``Database`` wrapper around the same
    path).
    """
    from job_bot.application_prep.slice import (
        create_application_prep_slice,
    )
    from job_bot.application_submit.slice import (
        create_application_submit_slice,
    )
    from job_bot.config_auth.slice import create_config_auth_slice
    from job_bot.max_bot.slice import create_max_bot_slice
    from job_bot.shared.config.settings import Settings
    from job_bot.shared.storage.database import create_database
    from job_bot.telegram_bot.slice import create_telegram_bot_slice
    from job_bot.vacancy_search.slice import create_vacancy_search_slice

    database = create_database(test_db_path)
    config_path = test_db_path.parent / "config.json"

    config_slice = create_config_auth_slice(
        settings=Settings(),
        database=database,
        config_path=config_path,
    )

    vacancy_slice = create_vacancy_search_slice(
        settings=Settings(),
        database=database,
        api_client=mock_hh_api,  # type: ignore[arg-type]
    )

    prep_slice = create_application_prep_slice(
        settings=Settings(),
        database=database,
        api_client=mock_hh_api,  # type: ignore[arg-type]
        ai_client=mock_ai_client,  # type: ignore[arg-type]
    )

    submit_slice = create_application_submit_slice(
        storage_conn=test_db,
        api_client=mock_hh_api,  # type: ignore[arg-type]
        ai_client=mock_ai_client,  # type: ignore[arg-type]
    )

    # Telegram slice owns its own connection (documented contract).
    # Wire it against the *shared* in-memory connection so we have a
    # single source of truth, then close that connection in teardown.
    telegram_conn = open_test_connection(":memory:")
    try:
        telegram_database = create_database(":memory:")
        telegram_slice = create_telegram_bot_slice(
            database=telegram_database,
            transport=mock_telegram_transport,
            config={
                "telegram": {
                    "bot_token": "test-token",
                    "allowed_user_ids": [1],
                    "daily_digest_time": "10:00",
                }
            },
        )
    except Exception:
        telegram_conn.close()
        raise

    max_slice = create_max_bot_slice(transport=mock_max_transport)

    return SimpleNamespace(
        database=database,
        config_path=config_path,
        config=config_slice,
        vacancy_search=vacancy_slice,
        application_prep=prep_slice,
        application_submit=submit_slice,
        telegram_bot=telegram_slice,
        max_bot=max_slice,
    )
