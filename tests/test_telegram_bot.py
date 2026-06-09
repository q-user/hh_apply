from __future__ import annotations

from unittest.mock import MagicMock, Mock

from hh_applicant_tool.operations.telegram_bot import Operation
from hh_applicant_tool.telegram import (
    TelegramTransport,
    TelegramTransportConfig,
)


def _transport(allowed: tuple[int, ...] = ()) -> TelegramTransport:
    config = TelegramTransportConfig(
        bot_token="test-token",
        poll_timeout=30,
        allowed_user_ids=allowed,
    )
    return TelegramTransport(config=config)


def test_handle_update_start_command():
    op = Operation()
    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]

    tool = MagicMock()
    update = {
        "update_id": 1,
        "message": {
            "from": {"id": 123},
            "chat": {"id": 456},
            "text": "/start",
        },
    }

    op._handle_update(update, transport, tool)
    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    assert "Добро пожаловать" in transport.send_message.call_args[0][1]


def test_handle_update_help_command():
    op = Operation()
    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]

    tool = MagicMock()
    update = {
        "update_id": 2,
        "message": {
            "from": {"id": 123},
            "chat": {"id": 456},
            "text": "/help",
        },
    }

    op._handle_update(update, transport, tool)
    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    assert "Доступные команды" in transport.send_message.call_args[0][1]


def test_handle_update_status_command():
    op = Operation()
    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]

    tool = Mock()
    tool.storage.negotiations.count_total.return_value = 10
    tool.storage.skipped_vacancies.count_total.return_value = 5
    tool.storage.application_drafts.count_total.return_value = 3

    update = {
        "update_id": 3,
        "message": {
            "from": {"id": 123},
            "chat": {"id": 456},
            "text": "/status",
        },
    }

    op._handle_update(update, transport, tool)
    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]
    assert "Переговоры: 10" in text
    assert "Пропущено: 5" in text
    assert "Черновики: 3" in text


def test_access_denied_when_user_not_in_allowed_list():
    op = Operation()
    transport = _transport(allowed=(999,))
    transport.send_message = Mock()  # type: ignore[method-assign]

    tool = MagicMock()
    update = {
        "update_id": 4,
        "message": {
            "from": {"id": 123},
            "chat": {"id": 456},
            "text": "/start",
        },
    }

    op._handle_update(update, transport, tool)
    transport.send_message.assert_called_once_with(456, "⛔ Доступ запрещён.")  # type: ignore[unused-coroutine]


def test_unknown_command_sends_help():
    op = Operation()
    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]

    tool = MagicMock()
    update = {
        "update_id": 5,
        "message": {
            "from": {"id": 123},
            "chat": {"id": 456},
            "text": "/unknown",
        },
    }

    op._handle_update(update, transport, tool)
    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    assert "Неизвестная команда" in transport.send_message.call_args[0][1]
    assert "/help" in transport.send_message.call_args[0][1]


def test_non_text_message():
    op = Operation()
    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]

    tool = MagicMock()
    update = {
        "update_id": 6,
        "message": {
            "from": {"id": 123},
            "chat": {"id": 456},
            "photo": [{"file_id": "abc"}],
        },
    }

    op._handle_update(update, transport, tool)
    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    assert "текстовые команды" in transport.send_message.call_args[0][1]
