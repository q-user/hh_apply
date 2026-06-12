"""MaxBotService -- orchestrator for the MAX Bot slice (issue #60).

Routes incoming updates to the :class:`CommandHandler` and ensures
errors are handled gracefully. Mirrors the Telegram-side
``BotService`` shape (``dispatch_update`` returns the outgoing
message DTO or ``None`` for non-message updates) so the two slices
can be operated the same way from the CLI / test harness.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from job_bot.max_bot.handlers.command_handler import CommandHandler
from job_bot.max_bot.models.message import OutgoingMessage

logger = logging.getLogger(__package__)


class _StorageLike(Protocol):
    """Forwarder for the storage port (see command_handler)."""


class MaxBotService:
    """Routes updates through the :class:`CommandHandler`.

    Args:
        command_handler: the configured command handler.
    """

    def __init__(self, *, command_handler: CommandHandler) -> None:
        self._command_handler = command_handler

    @property
    def command_handler(self) -> CommandHandler:
        """Underlying :class:`CommandHandler` (for tests)."""
        return self._command_handler

    def dispatch_update(
        self, update: dict[str, Any]
    ) -> OutgoingMessage | None:
        """Dispatch a single update through the command handler.

        Returns the outgoing :class:`OutgoingMessage` (already
        delivered via the transport by the handler) or ``None`` for
        updates the handler chose to ignore.
        """
        try:
            return self._command_handler.handle(update)
        except Exception:  # noqa: BLE001 - never crash the bot
            logger.exception(
                "MaxBotService.dispatch_update failed for update=%r",
                update,
            )
            return None


def create_max_bot_service(
    *,
    command_handler: CommandHandler,
) -> MaxBotService:
    """Factory for :class:`MaxBotService`.

    Kept symmetric with the Telegram-side ``create_bot_service`` so
    the two slices can be wired the same way from the CLI / container.
    """
    return MaxBotService(command_handler=command_handler)
