"""Tests for TelegramBotSlice wiring through the slim AppContainer (issue #155).

The new :class:`job_bot.container.AppContainer` is a pure-VSA composition
root. It exposes a ``telegram_bot`` :func:`@cached_property` slice accessor
(plus a ``create_telegram_bot_adapter`` factory in
:mod:`job_bot.telegram_bot.adapter` that the CLI operation uses). The 4
legacy ``_Adapter`` shim classes (``_TelegramBotAdapter`` was a thin
facade around the slice) are deleted from the container itself; the
adapter is now built by the slice package.

Verifies that:

1. ``AppContainer.telegram_bot`` returns a VSA slice (issue #56).
2. ``container.telegram_bot`` is a lazy singleton: repeated accesses
   return the same instance.
3. The slice is built against ``tool.db`` — no extra connection is
   opened against the same SQLite file.
4. The ``create_telegram_bot_adapter(slice_)`` factory from
   :mod:`job_bot.telegram_bot.adapter` produces the operation-facing
   ``TelegramBotAdapter`` (preserves the dispatch_update / send_digest
   surface the ``telegram-bot`` CLI operation depends on).
5. ``container.telegram_bot`` raises ``RuntimeError`` when ``bot_token``
   is missing from the config (the polling loop would be useless
   without it).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest


def _make_temp_db_path() -> str:
    """Create a temporary file path suitable for ``Database(path)``."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def temp_db_path() -> str:
    path = _make_temp_db_path()
    yield path
    _safe_unlink(path)


class TestTelegramBotSliceWiring:
    """Tests that the slim :class:`AppContainer` wires the
    :class:`TelegramBotSlice` via the ``telegram_bot`` cached_property
    (issue #155)."""

    def _make_mock_tool(
        self,
        temp_db_path: str,
        *,
        bot_token: str = "test-token",
    ) -> MagicMock:
        """Create a mock ``HHApplicantTool`` with all required attributes."""
        from hh_applicant_tool.main import HHApplicantTool

        with patch.object(HHApplicantTool, "__init__", lambda self: None):
            tool = HHApplicantTool()
            tool.config = {
                "telegram": {
                    "bot_token": bot_token,
                    "poll_timeout": 30,
                    "allowed_user_ids": [123],
                    "digest_chat_id": 42,
                    "daily_digest_time": "10:00",
                },
            }
            tool.db_path = temp_db_path
            # ``tool.db`` is a real ``sqlite3.Connection`` so the slice
            # gets the same live connection (no extra file handle).
            tool.db = sqlite3.connect(temp_db_path, check_same_thread=False)
            tool.storage = MagicMock()
            tool.session = MagicMock()
            tool.api_client = MagicMock()
            return tool

    def test_app_container_creates_telegram_bot_slice(
        self, temp_db_path: str
    ) -> None:
        """``AppContainer.telegram_bot`` returns a :class:`TelegramBotSlice`."""
        from job_bot.container import AppContainer
        from job_bot.telegram_bot.slice import TelegramBotSlice

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        slice_ = container.telegram_bot

        assert isinstance(slice_, TelegramBotSlice)
        # Public surface of the slice (issue #56).
        assert hasattr(slice_, "service")
        assert hasattr(slice_, "digest")
        assert hasattr(slice_, "review")
        assert hasattr(slice_, "transport")
        assert hasattr(slice_, "commands")

    def test_adapter_factory_wraps_the_slice(self, temp_db_path: str) -> None:
        """``create_telegram_bot_adapter(slice_)`` (from
        :mod:`job_bot.telegram_bot.adapter`) returns a
        :class:`TelegramBotAdapter` that exposes the operation-facing
        surface.

        Issue #155 removed ``AppContainer.create_telegram_bot_adapter``
        — the adapter is now built by the slice package. The CLI
        ``telegram-bot`` operation still uses the adapter shape.
        """
        from job_bot.container import AppContainer
        from job_bot.telegram_bot.adapter import (
            TelegramBotAdapter,
            create_telegram_bot_adapter,
        )

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        slice_ = container.telegram_bot

        adapter = create_telegram_bot_adapter(slice_)
        assert isinstance(adapter, TelegramBotAdapter)
        # The operation-facing surface (issue #56).
        assert hasattr(adapter, "transport")
        assert hasattr(adapter, "dispatch_update")
        assert hasattr(adapter, "send_digest")
        assert hasattr(adapter, "close")
        # And the adapter reuses the slice's transport.
        assert adapter.transport is slice_.transport

    def test_slice_is_lazy_singleton(self, temp_db_path: str) -> None:
        """``container.telegram_bot`` returns the same instance on repeat
        accesses (``@cached_property``)."""
        from job_bot.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        slice_a = container.telegram_bot
        slice_b = container.telegram_bot
        assert slice_a is slice_b

    def test_adapter_dispatch_update_delegates_to_slice(
        self, temp_db_path: str
    ) -> None:
        """``TelegramBotAdapter.dispatch_update`` calls
        ``slice.service.dispatch_update`` (the call that replaces the
        legacy ``Operation._handle_update`` switch in the CLI)."""
        from job_bot.container import AppContainer
        from job_bot.telegram_bot.adapter import create_telegram_bot_adapter

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        adapter = create_telegram_bot_adapter(container.telegram_bot)

        # Spy on the underlying BotService.
        bot_service = adapter.bot_service
        bot_service.dispatch_update = MagicMock(return_value="ok")  # type: ignore[method-assign]

        update = {"update_id": 1, "message": {"text": "/start"}}
        result = adapter.dispatch_update(update)

        bot_service.dispatch_update.assert_called_once_with(update)  # type: ignore[attr-defined]
        assert result == "ok"

    def test_adapter_send_digest_delegates_to_slice(
        self, temp_db_path: str
    ) -> None:
        """``TelegramBotAdapter.send_digest`` calls
        ``slice.digest.send``. The slice's ``DailyDigestService`` is the
        single source of truth for the daily-digest send + idempotency
        contract."""
        from job_bot.container import AppContainer
        from job_bot.telegram_bot.adapter import create_telegram_bot_adapter

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        adapter = create_telegram_bot_adapter(container.telegram_bot)

        digest = adapter.slice.digest
        digest.send = MagicMock(return_value="digest-result")  # type: ignore[method-assign]

        result = adapter.send_digest(force=True)

        digest.send.assert_called_once_with(force=True)  # type: ignore[attr-defined]
        assert result == "digest-result"

    def test_slice_uses_existing_tool_db_connection(
        self, temp_db_path: str
    ) -> None:
        """The slice is built against ``tool.db``; no new connection is opened.

        Issue #56 acceptance criteria: wiring must not create a
        second ``sqlite3.Connection`` against the same DB file (would
        lead to surprising lock contention in WAL mode).
        """
        from job_bot.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        # Sanity check: ``tool.db`` is open and the file exists.
        original_db = tool.db
        container = AppContainer(tool)
        slice_ = container.telegram_bot

        # The slice stores the same connection object via
        # ``_resolve_storage`` (which returns a raw
        # ``sqlite3.Connection`` unchanged). The slice's
        # ``.database`` property is the wrapper we passed in (or the
        # connection if it was a raw ``sqlite3.Connection``).
        assert slice_.database is original_db

    def test_no_bot_token_raises_clear_error(self, temp_db_path: str) -> None:
        """``container.telegram_bot`` raises ``RuntimeError`` when
        ``bot_token`` is missing from the config."""
        from job_bot.container import AppContainer

        tool = self._make_mock_tool(temp_db_path, bot_token="")
        container = AppContainer(tool)

        with pytest.raises(RuntimeError, match="bot_token"):
            _ = container.telegram_bot
