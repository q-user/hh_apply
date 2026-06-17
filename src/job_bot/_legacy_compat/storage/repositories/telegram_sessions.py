from __future__ import annotations

from ..models.telegram_session import TelegramSessionModel
from .base import BaseRepository


class TelegramSessionsRepository(BaseRepository):
    __table__ = "telegram_sessions"
    model = TelegramSessionModel
    # PK — chat_id, не стандартный "id"
    pkey: str = "chat_id"
