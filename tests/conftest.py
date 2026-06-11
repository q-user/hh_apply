"""Pytest configuration: add scripts/ to sys.path so tests can import the
standalone ``start`` launcher.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


# ─── Shared CLI-operation test helpers (issue #58) ───────────────────
#
# These helpers are reused by ``test_operations_max_bot.py`` and
# ``test_issue_58_deprecation.py`` so the two test files don't have to
# copy/paste ``_SimpleTool``, ``_make_args`` and friends. The naming
# follows the project convention (single underscore for module-private
# helpers; pytest still imports them via the conftest hook).

class _SimpleTool:
    """Bare-bones stand-in for ``HHApplicantTool`` for operation tests.

    The ``max-bot`` operation only reads ``tool.config`` and, in the
    build path, ``tool.session``. We expose both as plain instance
    attrs to keep the wiring honest.
    """

    def __init__(self) -> None:
        self.config: dict[str, Any] = {
            "max": {
                "bot_token": "x",
                "api_url": "https://botapi.max.ru",
            },
        }
        self.session: Any = _NoopSession()


class _NoopSession:
    """A ``requests.Session``-like stand-in: no real HTTP, no real cookies."""


class _StubTransport:
    """A :class:`MaxTransportPort` stub recording interactions.

    Used by ``test_operations_max_bot.py`` to drive the slice without
    touching the network.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self._polls = 0

    def send_message(self, chat_id: int, text: str) -> bool:
        self.sent.append((chat_id, text))
        return True

    def get_updates(
        self, offset: int | None = None, timeout: int = 30
    ) -> list[dict[str, Any]]:
        self._polls += 1
        return []


def _make_args(
    *,
    once: bool = False,
    send_message: bool = False,
    chat_id: int | None = None,
    text: str | None = None,
) -> argparse.Namespace:
    """Build an ``argparse.Namespace`` matching the CLI surface of the
    ``max-bot`` operation (issue #58). Mirrors the same fields the
    ``HHApplicantTool`` parser sets on the namespace."""
    return argparse.Namespace(
        once=once,
        send_message=send_message,
        chat_id=chat_id,
        text=text,
        profile_id="default",
        config_dir=None,
        verbosity=0,
        api_delay=None,
        user_agent=None,
        proxy_url=None,
        openai_proxy_url=None,
        operation_run=None,
    )
