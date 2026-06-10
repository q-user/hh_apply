"""ReviewHandler -- thin orchestration around ``ReviewFlowService``.

The review state machine is the most complex piece of the bot. The slice
keeps it as a thin pass-through that delivers every outgoing message
returned by the underlying service.
"""

from __future__ import annotations

import logging
from typing import Any

from hh_applicant_tool.telegram.transport import TelegramTransportError

logger = logging.getLogger(__package__)


class ReviewHandler:
    """Forward updates to the review state machine and ship its replies."""

    def __init__(
        self,
        *,
        storage: Any,
        transport: Any,
        review_service: Any,
    ) -> None:
        self._storage = storage
        self._transport = transport
        self._review = review_service

    def process_message(self, update: dict[str, Any]) -> list[Any]:
        """Forward a text update; return and ship the outgoing messages."""
        messages = self._review.process_message(update)
        self._deliver(messages)
        return messages

    def process_callback(self, update: dict[str, Any]) -> list[Any]:
        """Forward a callback_query; return and ship the outgoing messages."""
        messages = self._review.process_callback(update)
        self._deliver(messages)
        return messages

    def resume_session(self, chat_id: int) -> list[Any]:
        """Resume a session for ``chat_id`` (used on bot startup)."""
        messages = self._review.resume_session(chat_id)
        self._deliver(messages)
        return messages

    # ─── Internal ─────────────────────────────────────────────

    def _deliver(self, messages: list[Any]) -> None:
        for msg in messages:
            text = getattr(msg, "text", "")
            chat_id = getattr(msg, "chat_id", None)
            if chat_id is None:
                continue
            try:
                self._transport.send_message(chat_id, text)
            except TelegramTransportError as exc:
                logger.error("Failed to send review message: %s", exc)
