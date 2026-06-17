"""TelegramTransportPort -- Protocol contract for the transport layer.

The slice's transport handler depends on this Protocol; the concrete
:class:`job_bot.telegram_bot.telegram_transport.TelegramTransport` is provided
by the producer of the slice (CLI / tests).
"""

from __future__ import annotations

from typing import Any, Protocol


class TelegramTransportPort(Protocol):
    """Minimal transport interface used by the slice.

    Matches the public surface of
    :class:`job_bot.telegram_bot.telegram_transport.TelegramTransport`.
    """

    def get_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        """Long-poll the Telegram Bot API for new updates."""
        ...

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        """Send a plain text message to ``chat_id``."""
        ...

    @property
    def allowed_user_ids(self) -> tuple[int, ...]:
        """Tuple of Telegram user ids permitted to talk to the bot.

        Empty tuple = no filtering (every user allowed).
        """
        ...

    @property
    def poll_timeout(self) -> int:
        """Long-poll timeout in seconds."""
        ...
