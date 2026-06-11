"""Tests for the Telegram bot command-handling logic (issue #56).

The CLI ``Operation`` now delegates to the VSA ``TelegramBotSlice``
via ``TelegramBotAdapter``. The unit under test is the slice's
``BotService.dispatch_update`` (command routing, access control,
review-flow hand-off, non-text handling). The legacy
``Operation._handle_update`` no longer exists.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, Mock

from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.telegram import (
    TelegramTransport,
    TelegramTransportConfig,
)
from job_bot.telegram_bot.services.bot_service import BotService

# ─── Helpers ─────────────────────────────────────────────────────────


def _transport(allowed: tuple[int, ...] = ()) -> TelegramTransport:
    """Build a real ``TelegramTransport`` with no network calls."""
    config = TelegramTransportConfig(
        bot_token="test-token",
        poll_timeout=30,
        allowed_user_ids=allowed,
    )
    return TelegramTransport(config=config)


def _build_bot_service(
    transport: TelegramTransport,
    storage: sqlite3.Connection,
) -> BotService:
    """Wire a ``BotService`` against the given transport and in-memory DB."""
    return BotService(
        storage=storage,
        transport=transport,
        digest_service=MagicMock(),
        review_service=None,
    )


def _update(text: str | None, user_id: int = 123, chat_id: int = 456) -> dict:
    message: dict = {
        "from": {"id": user_id},
        "chat": {"id": chat_id},
    }
    if text is not None:
        message["text"] = text
    else:
        # Non-text payload (photo).
        message["photo"] = [{"file_id": "abc"}]
    return {"update_id": 1, "message": message}


# ─── /start, /help, /status, unknown command ────────────────────────


def test_start_command(storage: sqlite3.Connection) -> None:
    """``/start`` returns a greeting + commands list."""
    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    svc = _build_bot_service(transport, storage)

    svc.dispatch_update(_update("/start"))

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    assert "Добро пожаловать" in transport.send_message.call_args[0][1]


def test_help_command(storage: sqlite3.Connection) -> None:
    """``/help`` returns the commands list."""
    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    svc = _build_bot_service(transport, storage)

    svc.dispatch_update(_update("/help"))

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    assert "Доступные команды" in transport.send_message.call_args[0][1]


def test_status_command(storage: sqlite3.Connection) -> None:
    """``/status`` returns negotiations / skipped / drafts counts."""
    facade = StorageFacade(storage)
    # Seed one of each so the counts are non-zero.
    from hh_applicant_tool.storage.models.application_draft import (
        ApplicationDraftModel,
    )
    from hh_applicant_tool.storage.repositories.negotiations import (
        NegotiationModel,
    )
    from hh_applicant_tool.storage.repositories.skipped_vacancies import (
        SkippedVacancyModel,
    )

    facade.negotiations.save(
        NegotiationModel(
            id=1,
            chat_id=1,
            state="invitation",
            vacancy_id=1,
            resume_id="r1",
        )
    )
    facade.skipped_vacancies.save(SkippedVacancyModel(vacancy_id=2, reason="x"))
    facade.application_drafts.save(
        ApplicationDraftModel(
            resume_id="r1",
            vacancy_id=3,
            status="prepared",
        ),
    )

    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    svc = _build_bot_service(transport, storage)

    svc.dispatch_update(_update("/status"))

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]
    assert "Переговоры: 1" in text
    assert "Пропущено: 1" in text
    assert "Черновики: 1" in text


def test_access_denied_when_user_not_in_allowed_list(
    storage: sqlite3.Connection,
) -> None:
    """Updates from a user not in ``allowed_user_ids`` are rejected."""
    transport = _transport(allowed=(999,))
    transport.send_message = Mock()  # type: ignore[method-assign]
    svc = _build_bot_service(transport, storage)

    svc.dispatch_update(_update("/start"))

    transport.send_message.assert_called_once_with(  # type: ignore[unused-coroutine]
        456, "⛔ Доступ запрещён."
    )


def test_unknown_command_sends_help(storage: sqlite3.Connection) -> None:
    """Unknown commands get a friendly hint message."""
    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    svc = _build_bot_service(transport, storage)

    svc.dispatch_update(_update("/unknown"))

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]
    assert "Неизвестная команда" in text
    assert "/help" in text


def test_non_text_message(storage: sqlite3.Connection) -> None:
    """Non-text updates (photo, sticker, voice) get a hint reply."""
    transport = _transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    svc = _build_bot_service(transport, storage)

    svc.dispatch_update(_update(text=None))

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    assert "текстовые команды" in transport.send_message.call_args[0][1]
