"""Pytest configuration for the VSA slice tests.

Shares common fixtures (in-memory SQLite, temp config path) so each
slice's test file can stay focused on its own behaviour.
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from job_bot.shared.storage.database import Database, create_database


@pytest.fixture
def temp_db_path() -> Path:
    """Create a temporary on-disk database path (so SQLite can ``mkdir``)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def database(temp_db_path: Path) -> Database:
    """Create a :class:`Database` instance backed by a temp file."""
    return create_database(temp_db_path)


@pytest.fixture
def storage_conn() -> Iterator[sqlite3.Connection]:
    """Fresh in-memory SQLite connection with the canonical schema initialised.

    The underlying services (``ApplyJobsRepository`` etc.) expect a
    ``sqlite3.Connection`` with the schema in place. This fixture is the
    shared equivalent of the project-level ``storage`` fixture in
    ``tests/conftest.py`` -- re-defined here so VSA tests can use it
    without depending on the legacy ``hh_applicant_tool`` import path.

    Issue #94: schema is initialised via ``init_db`` directly (not via
    ``StorageFacade(conn)``) so this fixture no longer pulls in the
    legacy facade class for a pure side-effect call.
    """
    from hh_applicant_tool.storage.utils import init_db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def temp_config_path(tmp_path: Path) -> Path:
    """Return a non-existent config file path inside a temp dir."""
    return tmp_path / "config.json"


@pytest.fixture(autouse=False)
def cleanup_db(temp_db_path: Path) -> Iterator[None]:
    """Best-effort cleanup of the temp DB after the test."""
    yield
    try:
        temp_db_path.unlink(missing_ok=True)
    except OSError:
        pass
