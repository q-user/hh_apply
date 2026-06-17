"""TransportHandler -- long-polling loop with reconnect / back-off.

Modeled on the Telegram transport handler: pulls updates from the
transport in a loop, hands each one to ``on_update`` and advances the
offset. On transport errors it backs off and continues.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from job_bot.max_bot.ports.transport_port import MaxTransportPort

logger = logging.getLogger(__package__)

# Back-off parameters (defaults match the Telegram slice).
_INITIAL_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_MAX_BACKOFF = 60.0

UpdateCallback = Callable[[dict[str, Any]], None]


class TransportHandler:
    """Long-polling loop over a :class:`MaxTransportPort`."""

    def __init__(
        self,
        *,
        transport: MaxTransportPort,
        on_update: UpdateCallback,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._transport = transport
        self._on_update = on_update
        self._sleep: Callable[[float], None] = sleep_fn or time.sleep

    def run(self, *, stop_after: int | None = None) -> None:
        """Run the polling loop.

        Args:
            stop_after: stop after this many successful poll iterations.
                ``None`` means run forever (production mode). Used by tests
                to bound runtime.
        """
        offset: int | None = None
        backoff = _INITIAL_BACKOFF
        iterations = 0
        while True:
            try:
                updates = self._transport.get_updates(offset=offset)
                backoff = _INITIAL_BACKOFF  # reset on success
            except Exception as exc:  # noqa: BLE001 - defensive
                logger.exception("Polling error: %s", exc)
                self._sleep(min(backoff, _MAX_BACKOFF))
                backoff *= _BACKOFF_FACTOR
                continue

            for update in updates:
                try:
                    self._on_update(update)
                except Exception:  # noqa: BLE001 - never kill the loop
                    logger.exception("Error handling update: %s", update)
                update_id = update.get("update_id")
                if update_id is not None:
                    offset = update_id + 1

            iterations += 1
            if stop_after is not None and iterations >= stop_after:
                return
