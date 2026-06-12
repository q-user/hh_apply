"""Tests for the MAX Bot command handler + service (issue #60)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from job_bot.max_bot.handlers.command_handler import CommandHandler
from job_bot.max_bot.models.command import (
    CMD_CANCEL,
    CMD_HELP,
    CMD_REVIEW,
    CMD_START,
    CMD_STATS,
    CMD_STATUS,
    Command,
)
from job_bot.max_bot.services.bot_service import (
    MaxBotService,
    create_max_bot_service,
)
from job_bot.max_bot.requests_transport import (
    MaxTransportError,
    RequestsMaxTransport,
)


# ─── Command parsing ───────────────────────────────────────────────


class TestCommand:
    def test_parse_start(self):
        cmd = Command.parse("/start")
        assert cmd is not None
        assert cmd.name == CMD_START
        assert cmd.args == ""

    def test_parse_help_with_args(self):
        cmd = Command.parse("/help me please")
        assert cmd is not None
        assert cmd.name == CMD_HELP
        assert cmd.args == "me please"

    def test_parse_stats(self):
        cmd = Command.parse("/stats")
        assert cmd is not None
        assert cmd.name == CMD_STATS

    def test_parse_status(self):
        cmd = Command.parse("/status")
        assert cmd is not None
        assert cmd.name == CMD_STATUS

    def test_parse_review(self):
        cmd = Command.parse("/review")
        assert cmd is not None
        assert cmd.name == CMD_REVIEW

    def test_parse_cancel(self):
        cmd = Command.parse("/cancel")
        assert cmd is not None
        assert cmd.name == CMD_CANCEL

    def test_parse_unknown_returns_none(self):
        assert Command.parse("/unknown") is None

    def test_parse_no_slash_returns_none(self):
        assert Command.parse("start") is None

    def test_parse_empty_returns_none(self):
        assert Command.parse("") is None
        assert Command.parse("/") is None

    def test_parse_case_insensitive(self):
        cmd = Command.parse("/START")
        assert cmd is not None
        assert cmd.name == CMD_START


# ─── CommandHandler ────────────────────────────────────────────────


def _make_update(text, user_id=123, chat_id=456):
    return {
        "update_id": 1,
        "message": {
            "from": {"id": user_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


class TestCommandHandler:
    def _make_handler(
        self,
        *,
        allowed_user_ids=(),
        storage=None,
        digest_service=None,
    ):
        transport = MagicMock()
        transport.allowed_user_ids = allowed_user_ids
        return CommandHandler(
            storage=storage,
            transport=transport,
            digest_service=digest_service,
        ), transport

    def test_start_greeting(self):
        handler, transport = self._make_handler()
        result = handler.handle(_make_update("/start"))
        assert result is not None
        assert result.chat_id == 456
        assert "Добро пожаловать" in result.text
        assert "/help" in result.text
        transport.send_message.assert_called_once()

    def test_help_lists_commands(self):
        handler, transport = self._make_handler()
        result = handler.handle(_make_update("/help"))
        assert result is not None
        assert "/start" in result.text
        assert "/stats" in result.text
        assert "/review" in result.text
        assert "/cancel" in result.text
        transport.send_message.assert_called_once()

    def test_stats_without_digest_returns_error(self):
        handler, transport = self._make_handler()
        result = handler.handle(_make_update("/stats"))
        assert result is not None
        assert "не инициализирован" in result.text

    def test_stats_with_empty_groups(self):
        digest = MagicMock()
        digest.collect_groups.return_value = []
        handler, _ = self._make_handler(digest_service=digest)
        result = handler.handle(_make_update("/stats"))
        assert result is not None
        assert "Нет подготовленных" in result.text

    def test_stats_with_groups(self):
        digest = MagicMock()

        class G:
            profile_name = "Backend"
            total = 5
            with_tests = 2
            without_tests = 3
            average_score = 80

        digest.collect_groups.return_value = [G()]
        handler, _ = self._make_handler(digest_service=digest)
        result = handler.handle(_make_update("/stats"))
        assert result is not None
        assert "Backend" in result.text
        assert "5" in result.text
        assert "с тестами: 2" in result.text

    def test_stats_digest_failure_returns_error(self):
        digest = MagicMock()
        digest.collect_groups.side_effect = RuntimeError("DB is on fire")
        handler, _ = self._make_handler(digest_service=digest)
        result = handler.handle(_make_update("/stats"))
        assert result is not None
        assert "Не удалось получить" in result.text

    def test_status_without_storage_returns_error(self):
        handler, _ = self._make_handler(storage=None)
        result = handler.handle(_make_update("/status"))
        assert result is not None
        assert "не инициализировано" in result.text

    def test_status_with_storage(self):
        storage = MagicMock()
        storage.negotiations.count_total.return_value = 7
        storage.skipped_vacancies.count_total.return_value = 3
        storage.application_drafts.count_total.return_value = 12
        handler, _ = self._make_handler(storage=storage)
        result = handler.handle(_make_update("/status"))
        assert result is not None
        assert "Переговоры: 7" in result.text
        assert "Пропущено: 3" in result.text
        assert "Черновики: 12" in result.text

    def test_status_storage_failure_returns_error(self):
        storage = MagicMock()
        storage.negotiations.count_total.side_effect = RuntimeError("db")
        handler, _ = self._make_handler(storage=storage)
        result = handler.handle(_make_update("/status"))
        assert result is not None
        assert "Не удалось получить" in result.text

    def test_review_placeholder(self):
        handler, _ = self._make_handler()
        result = handler.handle(_make_update("/review"))
        assert result is not None
        assert "Review" in result.text
        assert "issue #9" in result.text

    def test_cancel_placeholder(self):
        handler, _ = self._make_handler()
        result = handler.handle(_make_update("/cancel"))
        assert result is not None
        assert "Review" in result.text
        assert "issue #9" in result.text

    def test_unknown_command_returns_hint(self):
        handler, transport = self._make_handler()
        result = handler.handle(_make_update("/foo"))
        assert result is not None
        assert "Неизвестная команда" in result.text
        transport.send_message.assert_called_once()

    def test_non_text_message_returns_hint(self):
        handler, transport = self._make_handler()
        result = handler.handle(_make_update(""))
        assert result is not None
        assert "только текстовые команды" in result.text
        transport.send_message.assert_called_once()

    def test_access_control_denies_other_users(self):
        handler, _ = self._make_handler(allowed_user_ids=(999,))
        result = handler.handle(_make_update("/help", user_id=123))
        assert result is not None
        assert "Доступ запрещён" in result.text

    def test_access_control_allows_listed_user(self):
        handler, transport = self._make_handler(allowed_user_ids=(123,))
        result = handler.handle(_make_update("/help", user_id=123))
        assert result is not None
        assert "Добро пожаловать" not in result.text
        assert "/start" in result.text
        transport.send_message.assert_called_once()

    def test_update_without_chat_id_returns_none(self):
        handler, _ = self._make_handler()
        bad_update = {"update_id": 1, "message": {"text": "/start"}}
        assert handler.handle(bad_update) is None

    def test_send_failure_does_not_propagate(self):
        handler, transport = self._make_handler()
        transport.send_message.side_effect = RuntimeError("network")
        # Must not raise -- the bot keeps running.
        result = handler.handle(_make_update("/start"))
        assert result is not None
        assert "Добро пожаловать" in result.text


# ─── MaxBotService ─────────────────────────────────────────────────


class TestMaxBotService:
    def test_dispatch_returns_handler_result(self):
        transport = MagicMock()
        transport.allowed_user_ids = ()
        handler = CommandHandler(storage=None, transport=transport)
        service = MaxBotService(command_handler=handler)
        result = service.dispatch_update(_make_update("/help"))
        assert result is not None
        assert "/start" in result.text

    def test_dispatch_swallows_handler_exception(self):
        handler = MagicMock()
        handler.handle.side_effect = RuntimeError("oops")
        service = MaxBotService(command_handler=handler)
        result = service.dispatch_update(_make_update("/start"))
        assert result is None  # not propagated

    def test_create_factory(self):
        transport = MagicMock()
        handler = CommandHandler(storage=None, transport=transport)
        service = create_max_bot_service(command_handler=handler)
        assert isinstance(service, MaxBotService)
        assert service.command_handler is handler


# ─── RequestsMaxTransport (real) ───────────────────────────────────


class TestRequestsMaxTransport:
    """Smoke tests for the real ``RequestsMaxTransport`` (issue #60).

    These tests inject a fake ``requests.Session`` so we don't hit
    the real MAX API. The point is to lock in the contract:
    ``get_updates`` parses the JSON response into a list,
    ``send_message`` POSTs the body, errors raise
    :class:`MaxTransportError`, and 429 honours ``Retry-After``.
    """

    def _make_transport(self, sleep_fn=None):
        session = MagicMock()
        return RequestsMaxTransport(
            session=session,
            bot_token="test-token",
            api_url="https://botapi.max.ru",
            sleep_fn=sleep_fn,
        ), session

    def _json_response(self, status_code, payload=None, headers=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.content = b"x" if payload is not None else b""
        resp.json.return_value = payload
        resp.headers = headers or {}
        resp.text = "" if payload is None else str(payload)
        return resp

    def test_get_updates_returns_list(self):
        transport, session = self._make_transport()
        session.request.return_value = self._json_response(
            200, payload=[{"update_id": 1, "message": {"text": "hi"}}]
        )
        updates = transport.get_updates(offset=0, timeout=10)
        assert len(updates) == 1
        assert updates[0]["update_id"] == 1

    def test_get_updates_handles_envelope(self):
        """Some API versions wrap the list in ``{"updates": [...]}``."""
        transport, session = self._make_transport()
        session.request.return_value = self._json_response(
            200, payload={"updates": [{"update_id": 2}]}
        )
        updates = transport.get_updates()
        assert updates == [{"update_id": 2}]

    def test_get_updates_empty_on_unknown_shape(self):
        transport, session = self._make_transport()
        session.request.return_value = self._json_response(
            200, payload={"weird": "shape"}
        )
        assert transport.get_updates() == []

    def test_send_message_posts_body(self):
        transport, session = self._make_transport()
        session.request.return_value = self._json_response(
            200, payload={"ok": True}
        )
        assert transport.send_message(chat_id=42, text="hello") is True
        # Verify the POST was made with the right body and auth header.
        # ``method`` and ``url`` are passed positionally to
        # ``Session.request``; only ``json`` / ``headers`` / ``params``
        # / ``timeout`` are kwargs.
        call_args = session.request.call_args
        assert call_args.args[0] == "POST"
        assert call_args.args[1] == "https://botapi.max.ru/messages"
        assert call_args.kwargs["json"] == {"chat_id": 42, "text": "hello"}
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"

    def test_get_updates_sends_auth_header(self):
        transport, session = self._make_transport()
        session.request.return_value = self._json_response(200, payload=[])
        transport.get_updates()
        assert (
            session.request.call_args.kwargs["headers"]["Authorization"]
            == "Bearer test-token"
        )

    def test_get_updates_raises_on_5xx(self):
        transport, session = self._make_transport()
        session.request.return_value = self._json_response(
            500, payload={"error": "boom"}
        )
        with pytest.raises(MaxTransportError) as excinfo:
            transport.get_updates()
        assert excinfo.value.status_code == 500

    def test_429_honours_retry_after(self):
        sleeps: list[float] = []
        transport, session = self._make_transport(sleep_fn=sleeps.append)
        # First call: 429 with Retry-After. Second call: 200.
        responses = [
            self._json_response(429, headers={"Retry-After": "2"}),
            self._json_response(200, payload=[]),
        ]
        session.request.side_effect = responses
        # After the 429, the transport honours Retry-After by sleeping,
        # then raises (so the polling loop can back off).
        with pytest.raises(MaxTransportError):
            transport.get_updates()
        assert sleeps == [2.0]

    def test_429_retry_after_capped(self):
        """Absurdly large ``Retry-After`` is capped to 30s."""
        sleeps: list[float] = []
        transport, session = self._make_transport(sleep_fn=sleeps.append)
        session.request.return_value = self._json_response(
            429, headers={"Retry-After": "99999"}
        )
        with pytest.raises(MaxTransportError):
            transport.get_updates()
        assert sleeps == [30.0]

    def test_network_error_raises(self):
        transport, session = self._make_transport()
        session.request.side_effect = ConnectionError("net down")
        with pytest.raises(MaxTransportError) as excinfo:
            transport.get_updates()
        assert "net down" in str(excinfo.value)

    def test_missing_token_raises(self):
        session = MagicMock()
        with pytest.raises(ValueError):
            RequestsMaxTransport(
                session=session, bot_token="", api_url="x"
            )

    def test_empty_text_raises(self):
        transport, _ = self._make_transport()
        with pytest.raises(ValueError):
            transport.send_message(chat_id=1, text="")
