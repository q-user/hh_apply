"""Тесты репозитория telegram_sessions (issue #1)."""

from __future__ import annotations

import sqlite3

from job_bot._legacy_compat.storage.facade import StorageFacade
from job_bot._legacy_compat.storage.models.telegram_session import (
    TelegramSessionModel,
)


def test_upsert_by_chat_id(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.telegram_sessions.save(
        TelegramSessionModel(
            chat_id=123,
            user_id=42,
            state="idle",
        )
    )
    storage.commit()

    # Повторный save с тем же chat_id → UPSERT
    facade.telegram_sessions.save(
        TelegramSessionModel(
            chat_id=123,
            user_id=42,
            state="review_intro",
            draft_id=99,
        )
    )
    storage.commit()

    assert facade.telegram_sessions.count_total() == 1
    fetched = facade.telegram_sessions.get(123)
    assert fetched is not None
    assert fetched.state == "review_intro"
    assert int(fetched.draft_id) == 99


def test_payload_json_roundtrip(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    payload = {
        "regen_target": "test_answer",
        "comment": "слишком коротко",
        "attempt": 2,
    }
    facade.telegram_sessions.save(
        TelegramSessionModel(
            chat_id=1,
            state="awaiting_test_regen_comment",
            payload_json=payload,
        )
    )
    storage.commit()

    fetched = facade.telegram_sessions.get(1)
    assert fetched.payload_json == payload


def test_multiple_chat_ids(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.telegram_sessions.save(
        TelegramSessionModel(chat_id=1, state="review_intro", draft_id=10)
    )
    facade.telegram_sessions.save(
        TelegramSessionModel(chat_id=2, state="review_test_answer")
    )
    storage.commit()

    assert facade.telegram_sessions.count_total() == 2

    reviewing_intro = list(facade.telegram_sessions.find(state="review_intro"))
    assert len(reviewing_intro) == 1
    assert reviewing_intro[0].chat_id == 1


def test_fsm_state_advances(storage: sqlite3.Connection):
    """Симулируем прогон FSM: idle → review_intro → review_test_answer → ..."""
    facade = StorageFacade(storage)
    facade.telegram_sessions.save(TelegramSessionModel(chat_id=1, state="idle"))
    storage.commit()

    states = [
        "review_intro",
        "review_test_answer",
        "review_cover_letter",
        "confirm_apply",
    ]
    for s in states:
        facade.telegram_sessions.save(TelegramSessionModel(chat_id=1, state=s))
        storage.commit()

    fetched = facade.telegram_sessions.get(1)
    assert fetched.state == "confirm_apply"
