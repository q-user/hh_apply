"""MaxBotSlice -- main entry point and factory (issue #60).

The slice aggregates the transport, the long-polling handler, and
the :class:`MaxBotService` orchestrator. The orchestrator wires the
:class:`CommandHandler` so the polling loop can dispatch incoming
text messages to the right reply builder (mirrors the Telegram
slice's wiring).

Usage::

    from job_bot.max_bot.slice import create_max_bot_slice

    slice_ = create_max_bot_slice(
        transport=my_transport,
        storage=my_storage,
        allowed_user_ids=(123,),
    )
    slice_.send_message(chat_id=123, text="hi")
    slice_.handler.run()
"""

from __future__ import annotations

import logging
from typing import Any

from job_bot.max_bot.handlers.command_handler import (
    CommandHandler,
    _StorageLike,
)
from job_bot.max_bot.handlers.transport_handler import (
    TransportHandler,
    UpdateCallback,
)
from job_bot.max_bot.ports.transport_port import MaxTransportPort
from job_bot.max_bot.services.bot_service import MaxBotService

logger = logging.getLogger(__package__)


def _default_on_update(_update: dict[str, Any]) -> None:
    """Default no-op update callback.

    Production wiring replaces this with the
    :class:`MaxBotService` orchestrator; the default keeps the
    :class:`TransportHandler` callable in tests / smoke runs.
    """
    return None


class MaxBotSlice:
    """Aggregates the MAX transport, handler, and bot service.

    The slice keeps a single :class:`MaxTransportPort`, a
    :class:`TransportHandler` that drives it, and a
    :class:`MaxBotService` that routes incoming updates to the
    command handler. A thin :meth:`send_message` shortcut is
    provided so callers don't have to reach through
    ``slice_.transport`` for the most common operation.
    """

    def __init__(
        self,
        *,
        transport: MaxTransportPort | Any,
        handler: TransportHandler | None = None,
        on_update: UpdateCallback | None = None,
        storage: _StorageLike | None = None,
        allowed_user_ids: tuple[int, ...] = (),
        command_handler: CommandHandler | None = None,
        service: MaxBotService | None = None,
    ) -> None:
        self._transport = transport
        self._on_update: UpdateCallback = on_update or _default_on_update

        # Build the command handler + service if not injected.
        if command_handler is None:
            command_handler = CommandHandler(
                storage=storage,
                transport=transport,
                digest_service=None,
            )
        self._command_handler = command_handler
        self._service = service or MaxBotService(
            command_handler=command_handler,
        )

        self._handler = handler or TransportHandler(
            transport=transport,
            on_update=self._on_update,
        )

        # Mirror ``allowed_user_ids`` onto the transport so the
        # command handler's access-control check works without a
        # dedicated ``TelegramTransport``-style config object.
        if allowed_user_ids and not getattr(
            transport, "allowed_user_ids", None
        ):
            try:
                transport.allowed_user_ids = tuple(allowed_user_ids)
            except (AttributeError, TypeError):
                # Transport doesn't accept the attribute (e.g. test
                # stub with a spec) -- skip silently. The command
                # handler will then fall through to "allow all".
                logger.debug(
                    "Transport does not accept allowed_user_ids; "
                    "access control disabled for this slice"
                )

    # ─── Public surface ───────────────────────────────────────

    @property
    def transport(self) -> MaxTransportPort | Any:
        return self._transport

    @property
    def handler(self) -> TransportHandler:
        return self._handler

    @property
    def service(self) -> MaxBotService:
        """Underlying :class:`MaxBotService` (for tests / advanced use)."""
        return self._service

    @property
    def command_handler(self) -> CommandHandler:
        """Underlying :class:`CommandHandler` (for tests)."""
        return self._command_handler

    def send_message(self, chat_id: int, text: str) -> bool:
        """Forward a ``send_message`` call to the underlying transport."""
        return self._transport.send_message(chat_id=chat_id, text=text)

    def dispatch_update(self, update: dict[str, Any]) -> Any:
        """Convenience: forward ``update`` to the bot service."""
        return self._service.dispatch_update(update)


def create_max_bot_slice(
    *,
    transport: MaxTransportPort | Any,
    handler: TransportHandler | None = None,
    on_update: UpdateCallback | None = None,
    storage: _StorageLike | None = None,
    allowed_user_ids: tuple[int, ...] = (),
    command_handler: CommandHandler | None = None,
    service: MaxBotService | None = None,
) -> MaxBotSlice:
    """Factory for :class:`MaxBotSlice`.

    Args:
        transport: any object satisfying :class:`MaxTransportPort`
            (typically a :class:`RequestsMaxTransport` in production
            or a stub in tests).
        handler: optional pre-built :class:`TransportHandler` (used by
            tests to inject a stubbed sleep / custom callback).
        on_update: optional update callback forwarded to the default
            handler when ``handler`` is not provided.
        storage: optional storage port used by ``/status`` and
            ``/stats``. ``None`` means the command handler returns a
            "not configured" reply for those commands.
        allowed_user_ids: optional access-control list; mirrored onto
            ``transport.allowed_user_ids`` when the transport accepts
            the attribute.
        command_handler: optional pre-built :class:`CommandHandler`
            (tests).
        service: optional pre-built :class:`MaxBotService` (tests).
    """
    return MaxBotSlice(
        transport=transport,
        handler=handler,
        on_update=on_update,
        storage=storage,
        allowed_user_ids=allowed_user_ids,
        command_handler=command_handler,
        service=service,
    )
