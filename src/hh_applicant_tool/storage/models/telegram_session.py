from __future__ import annotations

from typing import Any

from .base import BaseModel, mapped


class TelegramSessionModel(BaseModel):
    """Состояние FSM интерактивного ревью для конкретного chat_id.

    `state` определяет, какое сообщение/кнопки бот покажет следующим;
    `payload_json` хранит произвольные данные состояния (например, черновик
    комментария для регенерации).
    """

    chat_id: int
    user_id: int | None = None
    state: str = "idle"
    draft_id: int | None = None
    current_test_answer_id: int | None = None
    payload_json: Any = mapped(store_json=True, default=None)
    updated_at: str | None = None
