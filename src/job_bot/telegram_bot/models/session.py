"""TelegramSession domain model (a thin wrapper around the legacy storage model).

The slice re-uses the canonical ``TelegramSessionModel`` from
``hh_applicant_tool.storage.models.telegram_session`` for persistence. This
wrapper provides a stable import path inside the slice and ``to_storage`` /
``from_storage`` adapters so the slice never imports the legacy model
directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hh_applicant_tool.storage.models.telegram_session import (
    TelegramSessionModel,
)


@dataclass
class TelegramSession:
    """FSM session for a single chat.

    Attributes:
        chat_id: chat the session belongs to.
        state: FSM state name (``"idle"``, ``"review_intro"``, ...).
        draft_id: currently bound application_drafts.id (or ``None``).
        user_id: optional Telegram user id.
        current_test_answer_id: in-flight test answer being reviewed.
        payload_json: arbitrary per-state payload (e.g. regen target).
        updated_at: ISO timestamp of last update.
    """

    chat_id: int
    state: str = "idle"
    draft_id: int | None = None
    user_id: int | None = None
    current_test_answer_id: int | None = None
    payload_json: Any = None
    updated_at: str | None = None

    def to_storage(self) -> TelegramSessionModel:
        """Convert to the legacy storage model for persistence."""
        return TelegramSessionModel(
            chat_id=self.chat_id,
            state=self.state,
            draft_id=self.draft_id,
            user_id=self.user_id,
            current_test_answer_id=self.current_test_answer_id,
            payload_json=self.payload_json,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_storage(cls, model: TelegramSessionModel) -> TelegramSession:
        """Build a :class:`TelegramSession` from a stored model."""
        return cls(
            chat_id=model.chat_id,
            state=model.state,
            draft_id=model.draft_id,
            user_id=model.user_id,
            current_test_answer_id=model.current_test_answer_id,
            payload_json=model.payload_json,
            updated_at=model.updated_at,
        )
