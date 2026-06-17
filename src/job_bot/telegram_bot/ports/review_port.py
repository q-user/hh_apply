"""ReviewFlowPort -- Protocol contract for the review state machine.

The slice's :class:`ReviewHandler` depends on this Protocol; the concrete
:class:`job_bot.telegram_bot.services.review_service.ReviewFlowService`
is provided by the slice (via the default factory) or by tests.
"""

from __future__ import annotations

from typing import Any, Protocol


class ReviewFlowPort(Protocol):
    """Interface used by the slice's review handler.

    Mirrors the public surface of
    :class:`job_bot.telegram_bot.services.review_service.ReviewFlowService`.
    """

    def process_message(self, update: dict[str, Any]) -> list[Any]:
        """Process a text message and return outgoing messages."""
        ...

    def process_callback(self, update: dict[str, Any]) -> list[Any]:
        """Process a ``callback_query`` and return outgoing messages."""
        ...

    def resume_session(self, chat_id: int) -> list[Any]:
        """Resume the FSM session for ``chat_id`` (used on bot startup)."""
        ...
