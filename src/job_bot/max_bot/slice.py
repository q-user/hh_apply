"""MaxBotSlice -- main entry point and factory.

The slice aggregates the transport and the long-polling handler. For
this iteration only the transport surface is wired in (the MAX Bot
API client is stubbed and not yet connected).

Usage::

    from job_bot.max_bot.slice import create_max_bot_slice

    slice_ = create_max_bot_slice(transport=my_transport)
    slice_.send_message(chat_id=123, text="hi")
    slice_.handler.run()
"""

from __future__ import annotations

import logging
from typing import Any

from job_bot.max_bot.handlers.transport_handler import (
    TransportHandler,
    UpdateCallback,
)
from job_bot.max_bot.ports.transport_port import MaxTransportPort

logger = logging.getLogger(__package__)


def _default_on_update(_update: dict[str, Any]) -> None:
    """Default no-op update callback.

    Production wiring will replace this with a real dispatcher
    (command / review / digest). Keeping the default in the slice
    means ``TransportHandler`` always has something to call.
    """
    return None


class MaxBotSlice:
    """Aggregates the MAX transport and the long-polling handler.

    The slice keeps a single ``MaxTransportPort`` (provided by the
    caller) and a :class:`TransportHandler` that drives it. A thin
    :meth:`send_message` shortcut is provided so callers don't have to
    reach through ``slice_.transport`` for the most common operation.
    """

    def __init__(
        self,
        *,
        transport: MaxTransportPort | Any,
        handler: TransportHandler | None = None,
        on_update: UpdateCallback | None = None,
    ) -> None:
        self._transport = transport
        self._on_update: UpdateCallback = on_update or _default_on_update
        self._handler = handler or TransportHandler(
            transport=transport,
            on_update=self._on_update,
        )

    # ─── Public surface ───────────────────────────────────────

    @property
    def transport(self) -> MaxTransportPort | Any:
        return self._transport

    @property
    def handler(self) -> TransportHandler:
        return self._handler

    def send_message(self, chat_id: int, text: str) -> bool:
        """Forward a ``send_message`` call to the underlying transport."""
        return self._transport.send_message(chat_id=chat_id, text=text)


def create_max_bot_slice(
    *,
    transport: MaxTransportPort | Any,
    handler: TransportHandler | None = None,
    on_update: UpdateCallback | None = None,
) -> MaxBotSlice:
    """Factory for :class:`MaxBotSlice`.

    Args:
        transport: any object satisfying :class:`MaxTransportPort`
            (typically a stub in tests; the real client will live in
            ``hh_applicant_tool`` once the MAX integration lands).
        handler: optional pre-built :class:`TransportHandler` (used by
            tests to inject a stubbed sleep / custom callback).
        on_update: optional update callback forwarded to the default
            handler when ``handler`` is not provided.
    """
    return MaxBotSlice(
        transport=transport,
        handler=handler,
        on_update=on_update,
    )
