"""Tests for TelegramBotSlice wiring through AppContainer (VSA migration #56).

Verifies that:
  1. AppContainer can create the new TelegramBotSlice (issue #56).
  2. AppContainer can create an adapter that wraps the new slice and
     exposes the operation-facing surface (``transport``,
     ``dispatch_update``, ``send_digest``).
  3. The adapter re-uses the slice's underlying ``TelegramTransport``
     so the SOCKS5 proxy / retry logic from issue #47 keeps working.
  4. The slice is built against ``tool.db`` — no extra connection is
     opened against the same SQLite file.
  5. ``_get_telegram_bot_slice`` is a lazy singleton: repeated calls
     return the same instance.
  6. ``create_telegram_bot_adapter`` is a lazy singleton too: repeated
     calls return the same instance and re-use the underlying slice.
  7. The adapter's ``send_digest`` delegates to the slice's
     ``DailyDigestService.send`` (not a fresh, re-implemented version).
  8. The adapter's ``dispatch_update`` delegates to the slice's
     ``BotService.dispatch_update`` (replaces the legacy
     ``Operation._handle_update`` switch).
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
    """Tests that TelegramBotSlice is properly wired into the runtime."""

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
        """AppContainer can create a ``TelegramBotSlice`` instance."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        slice_ = container._get_telegram_bot_slice()

        assert slice_ is not None
        # Public surface of the slice (issue #56).
        assert hasattr(slice_, "service")
        assert hasattr(slice_, "digest")
        assert hasattr(slice_, "review")
        assert hasattr(slice_, "transport")
        assert hasattr(slice_, "commands")

    def test_app_container_creates_telegram_bot_adapter(
        self, temp_db_path: str
    ) -> None:
        """AppContainer can create an adapter wrapping the new slice."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        adapter = container.create_telegram_bot_adapter()

        assert adapter is not None
        # The operation-facing surface (issue #56).
        assert hasattr(adapter, "transport")
        assert hasattr(adapter, "dispatch_update")
        assert hasattr(adapter, "send_digest")
        assert hasattr(adapter, "close")

    def test_adapter_reuses_slice_transport(self, temp_db_path: str) -> None:
        """``adapter.transport`` is the slice's underlying transport.

        The SOCKS5 proxy + retry logic from issue #47 is owned by
        ``TelegramTransport``; the adapter must expose the same
        instance so the polling loop can drive it directly.
        """
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        adapter = container.create_telegram_bot_adapter()

        assert (
            adapter.transport is container._get_telegram_bot_slice().transport
        )

    def test_slice_is_lazy_singleton(self, temp_db_path: str) -> None:
        """``_get_telegram_bot_slice`` returns the same instance on repeat calls."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        slice_a = container._get_telegram_bot_slice()
        slice_b = container._get_telegram_bot_slice()
        assert slice_a is slice_b

    def test_adapter_is_lazy_singleton(self, temp_db_path: str) -> None:
        """``create_telegram_bot_adapter`` returns the same instance on repeat calls."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        adapter_a = container.create_telegram_bot_adapter()
        adapter_b = container.create_telegram_bot_adapter()
        assert adapter_a is adapter_b

    def test_adapter_dispatch_update_delegates_to_slice(
        self, temp_db_path: str
    ) -> None:
        """``adapter.dispatch_update`` calls ``slice.service.dispatch_update``.

        This is the single call that replaces the legacy
        ``Operation._handle_update`` switch in the CLI.
        """
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        adapter = container.create_telegram_bot_adapter()

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
        """``adapter.send_digest`` calls ``slice.digest.send``.

        The slice's ``DailyDigestService`` is the single source of
        truth for the daily-digest send + idempotency contract.
        """
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        container = AppContainer(tool)
        adapter = container.create_telegram_bot_adapter()

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
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool(temp_db_path)
        # Sanity check: ``tool.db`` is open and the file exists.
        original_db = tool.db
        container = AppContainer(tool)
        slice_ = container._get_telegram_bot_slice()

        # The slice stores the same connection object via
        # ``_resolve_storage`` (which returns a raw
        # ``sqlite3.Connection`` unchanged). The slice's
        # ``.database`` property is the wrapper we passed in (or the
        # connection if it was a raw ``sqlite3.Connection``).
        assert slice_.database is original_db

    def test_no_bot_token_raises_clear_error(self, temp_db_path: str) -> None:
        """``_get_telegram_bot_slice`` raises ``RuntimeError`` when ``bot_token`` is missing."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool(temp_db_path, bot_token="")
        # Bypass the early-exit path: build the container directly
        # and call the slice factory (the operation does the early
        # ``bot_token`` check itself; the container is the post-check
        # factory).
        container = AppContainer(tool)

        with pytest.raises(RuntimeError, match="bot_token"):
            container._get_telegram_bot_slice()
