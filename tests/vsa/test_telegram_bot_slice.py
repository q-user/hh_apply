"""Tests for the telegram_bot slice (VSA - Issue #50, Phase 3).

TDD: tests are written first, then the slice is implemented to make them pass.

Slice responsibilities:
  * Long-polling transport loop with reconnect/back-off.
  * Command handling (/start, /help, /stats, /review, /cancel, unknown).
  * Daily digest scheduling + idempotency + force-send.
  * Review state machine (test answers, cover letter, regen, skip,
    approve -> enqueue apply job).
  * Access control via ``allowed_user_ids``.

The tests are split into:
  * Model tests (pure data -- no DB, no transport).
  * Handler tests (mocked transport, in-memory DB).
  * Slice integration tests.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from job_bot.shared.storage.database import Database

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

CHAT_ID = 12345
USER_ID = 999

# ``temp_db_path`` and ``database`` live in ``tests/vsa/conftest.py``.


@pytest.fixture
def storage_conn() -> sqlite3.Connection:
    """Return a fresh in-memory SQLite connection with the schema initialised.

    We re-use the canonical ``StorageFacade`` from ``hh_applicant_tool`` so
    the slice talks to the same tables the rest of the project uses.
    """
    from hh_applicant_tool.storage import StorageFacade

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    StorageFacade(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def transport() -> MagicMock:
    """Mocked :class:`TelegramTransport` with a default ``send_message`` reply."""
    from job_bot.telegram_bot.telegram_transport import TelegramTransport

    t = MagicMock(spec=TelegramTransport)
    t.allowed_user_ids = (USER_ID,)
    t.poll_timeout = 30
    t.send_message.return_value = {"message_id": 1, "ok": True}
    return t


def _text_update(text: str, chat_id: int = CHAT_ID, user_id: int = USER_ID):
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": user_id},
            "text": text,
        },
    }


def _callback_update(data: str, chat_id: int = CHAT_ID, update_id: int = 2):
    return {
        "update_id": update_id,
        "callback_query": {
            "data": data,
            "message": {"chat": {"id": chat_id}},
        },
    }


# ---------------------------------------------------------------------------
# Model tests -- Command / OutgoingMessage / TelegramSession
# ---------------------------------------------------------------------------


class TestCommandModel:
    """Test the Command value object used by the slice."""

    def test_command_construction(self) -> None:
        from job_bot.telegram_bot.models.command import Command

        cmd = Command(name="start", args=(), raw="/start")
        assert cmd.name == "start"
        assert cmd.args == ()
        assert cmd.raw == "/start"

    def test_command_with_args(self) -> None:
        from job_bot.telegram_bot.models.command import Command

        cmd = Command(name="review", args=("42",), raw="/review 42")
        assert cmd.name == "review"
        assert cmd.args == ("42",)

    def test_command_equality(self) -> None:
        from job_bot.telegram_bot.models.command import Command

        a = Command(name="start", args=(), raw="/start")
        b = Command(name="start", args=(), raw="/start")
        assert a == b

    def test_command_parse_from_text(self) -> None:
        from job_bot.telegram_bot.models.command import Command

        cmd = Command.parse("/help")
        assert cmd is not None
        assert cmd.name == "help"
        assert cmd.args == ()

    def test_command_parse_with_args(self) -> None:
        from job_bot.telegram_bot.models.command import Command

        cmd = Command.parse("/review some-id")
        assert cmd is not None
        assert cmd.name == "review"
        assert cmd.args == ("some-id",)

    def test_command_parse_strips_whitespace(self) -> None:
        from job_bot.telegram_bot.models.command import Command

        cmd = Command.parse("  /start  ")
        assert cmd is not None
        assert cmd.name == "start"

    def test_command_parse_unknown_text(self) -> None:
        from job_bot.telegram_bot.models.command import Command

        # Plain text is NOT a command -> parse() returns None
        assert Command.parse("hello world") is None

    def test_command_parse_empty(self) -> None:
        from job_bot.telegram_bot.models.command import Command

        assert Command.parse("") is None
        assert Command.parse("/") is None

    def test_command_names_constant(self) -> None:
        from job_bot.telegram_bot.models.command import (
            CMD_CANCEL,
            CMD_HELP,
            CMD_REVIEW,
            CMD_START,
            CMD_STATS,
            CMD_STATUS,
        )

        assert CMD_START == "start"
        assert CMD_HELP == "help"
        assert CMD_STATS == "stats"
        assert CMD_STATUS == "status"
        assert CMD_REVIEW == "review"
        assert CMD_CANCEL == "cancel"


class TestOutgoingMessageModel:
    """Test the OutgoingMessage DTO."""

    def test_construction(self) -> None:
        from job_bot.telegram_bot.models.message import OutgoingMessage

        msg = OutgoingMessage(chat_id=CHAT_ID, text="hello")
        assert msg.chat_id == CHAT_ID
        assert msg.text == "hello"
        assert msg.reply_markup == []
        assert msg.parse_mode is None

    def test_with_markup(self) -> None:
        from job_bot.telegram_bot.models.message import (
            InlineButton,
            OutgoingMessage,
        )

        buttons = [[InlineButton("Ok", callback_data="ok")]]
        msg = OutgoingMessage(chat_id=CHAT_ID, text="x", reply_markup=buttons)
        assert msg.reply_markup == buttons
        assert msg.reply_markup[0][0].callback_data == "ok"

    def test_inline_button_url(self) -> None:
        from job_bot.telegram_bot.models.message import InlineButton

        btn = InlineButton("Open", url="https://example.com")
        assert btn.callback_data is None
        assert btn.url == "https://example.com"


class TestTelegramSessionModel:
    """Test the TelegramSession domain model (a thin wrapper)."""

    def test_construction(self) -> None:
        from job_bot.telegram_bot.models.session import TelegramSession

        s = TelegramSession(chat_id=CHAT_ID, state="idle")
        assert s.chat_id == CHAT_ID
        assert s.state == "idle"
        assert s.draft_id is None

    def test_to_storage(self) -> None:
        from job_bot.telegram_bot.models.session import TelegramSession

        s = TelegramSession(chat_id=CHAT_ID, state="review_intro", draft_id=7)
        # ``to_storage`` produces a ``TelegramSessionModel`` (legacy model).
        from hh_applicant_tool.storage.models.telegram_session import (
            TelegramSessionModel,
        )

        m = s.to_storage()
        assert isinstance(m, TelegramSessionModel)
        assert m.chat_id == CHAT_ID
        assert m.state == "review_intro"
        assert m.draft_id == 7

    def test_from_storage(self) -> None:
        from hh_applicant_tool.storage.models.telegram_session import (
            TelegramSessionModel,
        )
        from job_bot.telegram_bot.models.session import TelegramSession

        m = TelegramSessionModel(
            chat_id=CHAT_ID, state="review_test", draft_id=42
        )
        s = TelegramSession.from_storage(m)
        assert s.chat_id == CHAT_ID
        assert s.state == "review_test"
        assert s.draft_id == 42


# ---------------------------------------------------------------------------
# Port tests
# ---------------------------------------------------------------------------


class TestPorts:
    """Test the slice exposes Protocol ports."""

    def test_transport_port_protocol(self) -> None:
        from job_bot.telegram_bot.ports.transport_port import (
            TelegramTransportPort,
        )

        # The protocol must declare the documented methods
        assert hasattr(TelegramTransportPort, "get_updates")
        assert hasattr(TelegramTransportPort, "send_message")
        assert hasattr(TelegramTransportPort, "allowed_user_ids")

    def test_digest_port_protocol(self) -> None:
        from job_bot.telegram_bot.ports.digest_port import DailyDigestPort

        assert hasattr(DailyDigestPort, "send")
        assert hasattr(DailyDigestPort, "collect_groups")

    def test_review_port_protocol(self) -> None:
        from job_bot.telegram_bot.ports.review_port import ReviewFlowPort

        assert hasattr(ReviewFlowPort, "process_message")
        assert hasattr(ReviewFlowPort, "process_callback")
        assert hasattr(ReviewFlowPort, "resume_session")


# ---------------------------------------------------------------------------
# Transport Handler tests
# ---------------------------------------------------------------------------


class TestTransportHandler:
    """Test the long-polling transport loop."""

    def _build(self, transport: MagicMock, *, on_update=None, sleep_fn=None):
        from job_bot.telegram_bot.handlers.transport_handler import (
            TransportHandler,
        )

        return TransportHandler(
            transport=transport,
            on_update=on_update or (lambda u: None),
            sleep_fn=sleep_fn,
        )

    def test_single_poll_dispatches_update(self, transport: MagicMock) -> None:
        transport.get_updates.return_value = [_text_update("/start")]
        captured: list[Any] = []
        handler = self._build(transport, on_update=captured.append)

        # 1 iteration then stop
        handler.run(stop_after=1)

        assert len(captured) == 1
        assert captured[0]["message"]["text"] == "/start"
        # Offset must advance past update_id; verify the call happened
        # and the captured update has update_id=1 (so next offset = 2).
        assert transport.get_updates.call_count == 1
        assert captured[0]["update_id"] == 1

    def test_empty_polls_still_advance(self, transport: MagicMock) -> None:
        transport.get_updates.return_value = []
        handler = self._build(transport)
        handler.run(stop_after=2)
        assert transport.get_updates.call_count == 2

    def test_reconnect_after_error(self, transport: MagicMock) -> None:
        from job_bot.telegram_bot.telegram_transport import (
            TelegramTransportError,
        )

        # First call fails, subsequent calls return [].
        # ``stop_after=2`` bounds the loop so we don't run forever.
        call_count = {"n": 0}

        def _side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise TelegramTransportError("boom")
            return []

        transport.get_updates.side_effect = _side_effect
        captured: list[Any] = []
        sleep_calls: list[float] = []
        handler = self._build(
            transport,
            on_update=captured.append,
            sleep_fn=sleep_calls.append,
        )
        handler.run(stop_after=2)

        # We got past the error (>= 2 polls happened)
        assert transport.get_updates.call_count >= 2
        # And we slept (back-off after error)
        assert len(sleep_calls) >= 1

    def test_update_dispatch_calls_on_update_with_full_update(
        self, transport: MagicMock
    ) -> None:
        # Build a custom update with update_id=99.
        upd = {
            "update_id": 99,
            "message": {
                "chat": {"id": CHAT_ID},
                "from": {"id": USER_ID},
                "text": "/help",
            },
        }
        # First call returns the update, second returns [].
        transport.get_updates.side_effect = [[upd], []]
        captured: list[Any] = []
        handler = self._build(transport, on_update=captured.append)
        handler.run(stop_after=2)  # 2 iterations so we see offset=100
        assert len(captured) == 1
        # Second get_updates call uses the advanced offset.
        assert transport.get_updates.call_args_list[1] == (
            (),
            {"offset": 100},
        )


# ---------------------------------------------------------------------------
# Command Handler tests
# ---------------------------------------------------------------------------


class TestCommandHandler:
    """Test command handling: /start, /help, /stats, /review, /cancel."""

    def _build(
        self,
        storage_conn: sqlite3.Connection,
        transport: MagicMock,
        *,
        digest_service: Any = None,
        review_service: Any = None,
    ):
        from job_bot.telegram_bot.handlers.command_handler import (
            CommandHandler,
        )

        return CommandHandler(
            storage=storage_conn,
            transport=transport,
            digest_service=digest_service,
            review_service=review_service,
        )

    def test_start_returns_greeting(self, transport, storage_conn) -> None:
        handler = self._build(storage_conn, transport)
        reply = handler.handle(_text_update("/start"))
        assert reply is not None
        assert "Добро пожаловать" in reply.text
        transport.send_message.assert_called_once()

    def test_help_returns_command_list(self, transport, storage_conn) -> None:
        handler = self._build(storage_conn, transport)
        reply = handler.handle(_text_update("/help"))
        assert reply is not None
        assert "/start" in reply.text
        assert "/help" in reply.text
        assert "/stats" in reply.text

    def test_stats_with_empty_db(self, transport, storage_conn) -> None:
        digest = MagicMock()
        digest.collect_groups.return_value = []
        handler = self._build(storage_conn, transport, digest_service=digest)
        reply = handler.handle(_text_update("/stats"))
        assert reply is not None
        assert (
            "Нет подготовленных черновиков" in reply.text or "0" in reply.text
        )

    def test_stats_with_prepared_drafts(self, transport, storage_conn) -> None:
        digest = MagicMock()
        digest.collect_groups.return_value = [
            MagicMock(
                profile_name="p1",
                total=1,
                with_tests=0,
                without_tests=1,
                average_score=80,
            )
        ]
        handler = self._build(storage_conn, transport, digest_service=digest)
        reply = handler.handle(_text_update("/stats"))
        assert reply is not None
        assert "1" in reply.text or "Черновики" in reply.text

    def test_review_invokes_review_service(
        self, transport, storage_conn
    ) -> None:
        review = MagicMock()
        review.process_message.return_value = []
        handler = self._build(storage_conn, transport, review_service=review)
        handler.handle(_text_update("/review"))
        review.process_message.assert_called_once()

    def test_cancel_invokes_review_service(
        self, transport, storage_conn
    ) -> None:
        review = MagicMock()
        review.process_message.return_value = []
        handler = self._build(storage_conn, transport, review_service=review)
        handler.handle(_text_update("/cancel"))
        review.process_message.assert_called_once()

    def test_unknown_command_returns_hint(
        self, transport, storage_conn
    ) -> None:
        handler = self._build(storage_conn, transport)
        reply = handler.handle(_text_update("hello world"))
        assert reply is not None
        assert "Неизвестная команда" in reply.text or "/help" in reply.text

    def test_access_denied_user(self, transport, storage_conn) -> None:
        # Transport allows only USER_ID (999), but message comes from 1
        bad_update = _text_update("/start", user_id=1)
        handler = self._build(storage_conn, transport)
        reply = handler.handle(bad_update)
        assert reply is not None
        assert "Доступ запрещён" in reply.text

    def test_access_allowed_user(self, transport, storage_conn) -> None:
        # USER_ID matches allowed_user_ids
        handler = self._build(storage_conn, transport)
        reply = handler.handle(_text_update("/start"))
        assert reply is not None
        assert "Доступ запрещён" not in reply.text

    def test_no_user_id_in_update(self, transport, storage_conn) -> None:
        # Update without from.id or chat.id => ignored, no message sent
        update = {
            "update_id": 1,
            "message": {"text": "/start"},  # no chat, no from
        }
        handler = self._build(storage_conn, transport)
        handler.handle(update)
        # Should not raise; may return None
        transport.send_message.assert_not_called()

    def test_empty_allowed_user_ids_allows_all(self, storage_conn) -> None:
        # If allowed_user_ids is empty -> no filtering
        from job_bot.telegram_bot.telegram_transport import TelegramTransport

        t = MagicMock(spec=TelegramTransport)
        t.allowed_user_ids = ()
        t.send_message.return_value = {"ok": True}
        handler = self._build(storage_conn, t)
        reply = handler.handle(_text_update("/start", user_id=1))
        assert "Добро пожаловать" in reply.text


# ---------------------------------------------------------------------------
# Digest Handler tests
# ---------------------------------------------------------------------------


class TestDigestHandler:
    """Test the daily digest handler (delegates to ``DailyDigestService``)."""

    def _build(
        self,
        storage_conn: sqlite3.Connection,
        transport: MagicMock,
        digest_service: Any,
    ):
        from job_bot.telegram_bot.handlers.digest_handler import (
            DigestHandler,
        )

        return DigestHandler(
            storage=storage_conn,
            transport=transport,
            digest_service=digest_service,
        )

    def test_send_invokes_service(self, transport, storage_conn) -> None:
        digest = MagicMock()
        digest.send.return_value = MagicMock(sent=True, total_drafts=3)
        handler = self._build(storage_conn, transport, digest)
        result = handler.send()
        digest.send.assert_called_once()
        assert result.sent is True

    def test_send_force(self, transport, storage_conn) -> None:
        digest = MagicMock()
        digest.send.return_value = MagicMock(sent=True, total_drafts=3)
        handler = self._build(storage_conn, transport, digest)
        handler.send(force=True)
        digest.send.assert_called_once_with(force=True)

    def test_send_idempotency(self, transport, storage_conn) -> None:
        # Second call in same day returns skipped

        digest = MagicMock()
        digest.send.side_effect = [
            MagicMock(sent=True, total_drafts=2),
            MagicMock(sent=False, skipped_reason="already_sent"),
        ]
        handler = self._build(storage_conn, transport, digest)
        first = handler.send()
        second = handler.send()
        assert first.sent is True
        assert second.sent is False
        assert second.skipped_reason == "already_sent"

    def test_empty_db_skips(self, transport, storage_conn) -> None:
        # The DailyDigestService has a real empty-DB path; here we just
        # assert the slice surfaces the service's DigestResult.

        digest = MagicMock()
        digest.send.return_value = MagicMock(
            sent=True, total_drafts=0, message="empty"
        )
        handler = self._build(storage_conn, transport, digest)
        result = handler.send()
        assert result.total_drafts == 0

    def test_collect_groups(self, transport, storage_conn) -> None:
        digest = MagicMock()
        digest.collect_groups.return_value = [
            MagicMock(
                profile_name="p1",
                total=2,
                with_tests=1,
                without_tests=1,
                average_score=80,
            )
        ]
        handler = self._build(storage_conn, transport, digest)
        groups = handler.collect_groups()
        assert len(groups) == 1
        assert groups[0].profile_name == "p1"

    def test_maybe_send_digest_gated_by_time(
        self, transport, storage_conn
    ) -> None:
        # If current time is before target time, no send.
        digest = MagicMock()
        handler = self._build(storage_conn, transport, digest)
        handler.maybe_send(
            config={"telegram": {"daily_digest_time": "23:59"}},
            now=datetime(2026, 6, 9, 9, 0, 0),
        )
        digest.send.assert_not_called()

    def test_maybe_send_digest_past_target(
        self, transport, storage_conn
    ) -> None:
        digest = MagicMock()
        digest.send.return_value = MagicMock(sent=True, total_drafts=5)
        handler = self._build(storage_conn, transport, digest)
        handler.maybe_send(
            config={"telegram": {"daily_digest_time": "08:00"}},
            now=datetime(2026, 6, 9, 10, 0, 0),
        )
        digest.send.assert_called_once()

    def test_maybe_send_no_telegram_config(
        self, transport, storage_conn
    ) -> None:
        digest = MagicMock()
        handler = self._build(storage_conn, transport, digest)
        handler.maybe_send(config={}, now=datetime(2026, 6, 9, 10, 0, 0))
        digest.send.assert_not_called()


# ---------------------------------------------------------------------------
# Review Handler tests
# ---------------------------------------------------------------------------


class TestReviewHandler:
    """Test the review state machine (delegates to ``ReviewFlowService``)."""

    def _build(
        self,
        storage_conn: sqlite3.Connection,
        transport: MagicMock,
        review_service: Any,
    ):
        from job_bot.telegram_bot.handlers.review_handler import (
            ReviewHandler,
        )

        return ReviewHandler(
            storage=storage_conn,
            transport=transport,
            review_service=review_service,
        )

    def test_process_message_delegates(self, transport, storage_conn) -> None:
        review = MagicMock()
        review.process_message.return_value = []
        handler = self._build(storage_conn, transport, review)
        handler.process_message(_text_update("anything"))
        review.process_message.assert_called_once()

    def test_process_callback_delegates(self, transport, storage_conn) -> None:
        review = MagicMock()
        review.process_callback.return_value = []
        handler = self._build(storage_conn, transport, review)
        handler.process_callback(_callback_update("rf:intro:continue"))
        review.process_callback.assert_called_once()

    def test_resume_session_delegates(self, transport, storage_conn) -> None:
        review = MagicMock()
        review.resume_session.return_value = []
        handler = self._build(storage_conn, transport, review)
        handler.resume_session(CHAT_ID)
        review.resume_session.assert_called_once_with(CHAT_ID)

    def test_send_outgoing_messages(self, transport, storage_conn) -> None:
        review = MagicMock()
        review.process_message.return_value = [
            MagicMock(chat_id=CHAT_ID, text="hi", reply_markup=[]),
            MagicMock(chat_id=CHAT_ID, text="bye", reply_markup=[]),
        ]
        handler = self._build(storage_conn, transport, review)
        handler.process_message(_text_update("/review"))
        # transport.send_message called twice (one per outgoing message)
        assert transport.send_message.call_count == 2


# ---------------------------------------------------------------------------
# Bot Service tests
# ---------------------------------------------------------------------------


class TestBotService:
    """Test the top-level orchestrator that wires handlers + transport."""

    def _build(
        self,
        storage_conn: sqlite3.Connection,
        transport: MagicMock,
        *,
        digest_service: Any = None,
        review_service: Any = None,
    ):
        from job_bot.telegram_bot.services.bot_service import BotService

        return BotService(
            storage=storage_conn,
            transport=transport,
            digest_service=digest_service,
            review_service=review_service,
        )

    def test_create_service(self, storage_conn, transport) -> None:
        service = self._build(storage_conn, transport)
        assert service is not None
        assert service.storage is storage_conn
        assert service.transport is transport

    def test_dispatch_message_to_command_handler(
        self, storage_conn, transport
    ) -> None:
        service = self._build(storage_conn, transport)
        service.dispatch_update(_text_update("/start"))
        transport.send_message.assert_called_once()
        assert "Добро пожаловать" in transport.send_message.call_args[0][1]

    def test_dispatch_message_to_review_when_active(
        self, storage_conn, transport
    ) -> None:
        # Pre-seed a session in 'awaiting_test_regen' state so the bot
        # routes the text message to the review service.
        from hh_applicant_tool.storage import StorageFacade
        from hh_applicant_tool.storage.models.telegram_session import (
            TelegramSessionModel,
        )

        StorageFacade(storage_conn).telegram_sessions.save(
            TelegramSessionModel(
                chat_id=CHAT_ID, state="awaiting_test_regen_comment"
            )
        )
        storage_conn.commit()

        review = MagicMock()
        review.process_message.return_value = []
        service = self._build(storage_conn, transport, review_service=review)
        service.dispatch_update(_text_update("some comment"))
        review.process_message.assert_called_once()

    def test_dispatch_callback_to_review(self, storage_conn, transport) -> None:
        review = MagicMock()
        review.process_callback.return_value = []
        service = self._build(storage_conn, transport, review_service=review)
        service.dispatch_update(_callback_update("rf:intro:continue"))
        review.process_callback.assert_called_once()

    def test_dispatch_ignores_update_without_message_or_callback(
        self, storage_conn, transport
    ) -> None:
        service = self._build(storage_conn, transport)
        # An update with neither message nor callback_query is silently dropped.
        service.dispatch_update({"update_id": 1})
        transport.send_message.assert_not_called()

    def test_error_during_dispatch_not_propagated(
        self, storage_conn, transport
    ) -> None:
        # A broken handler should not kill the loop.
        review = MagicMock()
        review.process_message.side_effect = RuntimeError("boom")
        service = self._build(storage_conn, transport, review_service=review)
        # Pre-seed an active state to force the review path
        from hh_applicant_tool.storage import StorageFacade
        from hh_applicant_tool.storage.models.telegram_session import (
            TelegramSessionModel,
        )

        StorageFacade(storage_conn).telegram_sessions.save(
            TelegramSessionModel(
                chat_id=CHAT_ID, state="awaiting_test_regen_comment"
            )
        )
        storage_conn.commit()
        # Should not raise
        service.dispatch_update(_text_update("x"))


# ---------------------------------------------------------------------------
# Slice + Factory tests
# ---------------------------------------------------------------------------


class TestTelegramBotSlice:
    """Test the slice aggregation and factory."""

    def test_create_slice(self, database: Database) -> None:
        from job_bot.telegram_bot.slice import TelegramBotSlice

        transport = MagicMock()
        slice_ = TelegramBotSlice(database=database, transport=transport)
        assert slice_.database is database
        assert slice_.transport is transport
        # Ports are accessible
        assert slice_.commands is not None
        assert slice_.digest is not None
        assert slice_.review is not None
        assert slice_.service is not None

    def test_create_slice_with_real_transport(self, database: Database) -> None:
        from job_bot.telegram_bot.slice import TelegramBotSlice

        transport = MagicMock()
        transport.allowed_user_ids = ()
        slice_ = TelegramBotSlice(database=database, transport=transport)
        # Storage facade should be wired for command / digest / review
        assert slice_.commands is not None
        assert slice_.digest is not None
        assert slice_.review is not None

    def test_create_slice_factory(self, database: Database) -> None:
        from job_bot.telegram_bot.slice import (
            TelegramBotSlice,
            create_telegram_bot_slice,
        )

        transport = MagicMock()
        slice_ = create_telegram_bot_slice(
            database=database, transport=transport
        )
        assert isinstance(slice_, TelegramBotSlice)
        assert slice_.database is database


# ---------------------------------------------------------------------------
# End-to-end integration test
# ---------------------------------------------------------------------------


class TestTelegramBotSliceIntegration:
    """End-to-end test: a message travels through the full slice."""

    def test_start_command_full_flow(self, database: Database) -> None:
        from job_bot.telegram_bot.slice import create_telegram_bot_slice

        transport = MagicMock()
        transport.allowed_user_ids = (USER_ID,)
        transport.send_message.return_value = {"ok": True}

        slice_ = create_telegram_bot_slice(
            database=database, transport=transport
        )
        slice_.service.dispatch_update(_text_update("/start"))
        transport.send_message.assert_called_once()
        text = transport.send_message.call_args[0][1]
        assert "Добро пожаловать" in text

    def test_unknown_command_full_flow(self, database: Database) -> None:
        from job_bot.telegram_bot.slice import create_telegram_bot_slice

        transport = MagicMock()
        transport.allowed_user_ids = (USER_ID,)
        transport.send_message.return_value = {"ok": True}

        slice_ = create_telegram_bot_slice(
            database=database, transport=transport
        )
        slice_.service.dispatch_update(_text_update("banana"))
        transport.send_message.assert_called_once()
        text = transport.send_message.call_args[0][1]
        assert "Неизвестная команда" in text or "/help" in text

    def test_access_denied_full_flow(self, database: Database) -> None:
        from job_bot.telegram_bot.slice import create_telegram_bot_slice

        transport = MagicMock()
        transport.allowed_user_ids = (USER_ID,)  # only USER_ID allowed
        transport.send_message.return_value = {"ok": True}

        slice_ = create_telegram_bot_slice(
            database=database, transport=transport
        )
        # USER_ID is 999, send as user_id=1
        slice_.service.dispatch_update(_text_update("/start", user_id=1))
        transport.send_message.assert_called_once()
        text = transport.send_message.call_args[0][1]
        assert "Доступ запрещён" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
