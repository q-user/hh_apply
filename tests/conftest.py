"""Pytest configuration: add scripts/ to sys.path so tests can import the
standalone ``start`` launcher.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def storage() -> Iterator[sqlite3.Connection]:
    """Свежая in-memory SQLite с инициализированной схемой.

    Возвращает сырой ``sqlite3.Connection`` — тесты могут изучать
    «сырое» состояние (наличие триггеров/PRAGMA) или обернуть в
    ``StorageFacade(conn)`` для доступа к репозиториям.
    """
    from hh_applicant_tool.storage import StorageFacade

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    StorageFacade(conn)
    try:
        yield conn
    finally:
        conn.close()
