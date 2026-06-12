"""``ChannelMonitorService`` -- orchestrates polling all enabled channels (issue #61).

The service is the single entry point for the slice. It iterates
enabled channels from :class:`ChannelHandler`, polls each one via
:class:`ChannelPoller`, marks new links as processed, and forwards them
to the configured :class:`NotificationPort`.

The service is designed to be driven by an external scheduler
(``APScheduler``, cron, etc.) -- it exposes :meth:`tick` for a single
poll cycle and :meth:`run` for an in-process loop. Both honour a
``stop_after`` parameter so tests can bound runtime.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from job_bot.channel_monitoring.handlers.channel_handler import ChannelHandler
from job_bot.channel_monitoring.ports.notification_port import NotificationPort
from job_bot.channel_monitoring.services.channel_poller import ChannelPoller
from job_bot.telegram_bot.ports.transport_port import TelegramTransportPort

logger = logging.getLogger(__name__)


# Default polling cadence (seconds). Overridable per service instance.
DEFAULT_TICK_INTERVAL = 30.0


class ChannelMonitorService:
    """Orchestrate polling of all enabled channels (issue #61).

    Args:
        transport: a :class:`TelegramTransportPort` (the same one the
            Telegram bot slice uses -- typically
            :class:`TelegramTransport`).
        handler: a :class:`ChannelHandler` bound to a
            ``sqlite3.Connection``.
        notifier: a :class:`NotificationPort` that delivers new vacancy
            links to a chat (Telegram / MAX / webhook).
        tick_interval: seconds between poll cycles (default 30).
        sleep_fn: optional override for the sleep helper (used by
            tests to bound :meth:`run` runtime).
        chat_id: target chat for notifications. When ``None`` the
            service falls back to the channel's own id (handy for
            admin channels); pass an explicit chat id to fan out to a
            private bot chat.
    """

    def __init__(
        self,
        *,
        transport: TelegramTransportPort,
        handler: ChannelHandler,
        notifier: NotificationPort,
        tick_interval: float = DEFAULT_TICK_INTERVAL,
        sleep_fn: Callable[[float], None] | None = None,
        chat_id: int | None = None,
    ) -> None:
        self._transport = transport
        self._handler = handler
        self._notifier = notifier
        self._tick_interval = max(0.0, float(tick_interval))
        self._sleep: Callable[[float], None] = sleep_fn or time.sleep
        self._chat_id = chat_id

    @property
    def handler(self) -> ChannelHandler:
        return self._handler

    @property
    def notifier(self) -> NotificationPort:
        return self._notifier

    def tick(self) -> int:
        """Run a single poll cycle across all enabled channels.

        Returns the number of new vacancy links delivered.
        """
        delivered = 0
        for channel in self._handler.list_channels(enabled_only=True):
            try:
                poller = ChannelPoller(
                    transport=self._transport,
                    channel=channel,
                    handler=self._handler,
                )
                new_links, next_offset = poller.poll_once()
            except Exception as exc:  # noqa: BLE001 -- one channel must not break the others
                logger.exception(
                    "ChannelMonitorService: poll failed for %s: %s",
                    channel.channel_id,
                    exc,
                )
                continue

            for link in new_links:
                self._deliver(link)
                self._handler.mark_processed(link)
                delivered += 1

            self._persist_offset(channel, next_offset)
        return delivered

    def run(self, *, stop_after: int | None = None) -> int:
        """Run the poll loop in-process.

        Args:
            stop_after: stop after this many ticks. ``None`` means run
                forever. Production should use an external scheduler
                (``APScheduler``) and call :meth:`tick` directly.

        Returns the total number of delivered links.
        """
        total = 0
        iterations = 0
        while True:
            total += self.tick()
            iterations += 1
            if stop_after is not None and iterations >= stop_after:
                return total
            if self._tick_interval > 0:
                self._sleep(self._tick_interval)

    # ─── Internals ────────────────────────────────────────────

    def _deliver(self, link: Any) -> None:
        """Send ``link`` via the notifier; never raise."""
        chat_id = self._chat_id
        if chat_id is None:
            # Fall back to the source channel id. Only works for
            # numeric ids; for ``@vacancies``-style names the caller
            # MUST pass ``chat_id=...`` to the service at construction
            # time.
            try:
                chat_id = int(str(link.source_channel).lstrip("@"))
            except (TypeError, ValueError):
                logger.debug(
                    "ChannelMonitorService: no chat_id configured and source %r "
                    "is not numeric; skipping delivery of %s. "
                    "Pass chat_id=... to the service for @-named channels.",
                    link.source_channel,
                    link.vacancy_id,
                )
                return
        try:
            self._notifier.send(chat_id, link)
        except Exception as exc:  # noqa: BLE001 -- defensive: notifier MUST NOT raise
            logger.exception(
                "ChannelMonitorService: notifier.send failed for %s: %s",
                link.vacancy_id,
                exc,
            )

    def _persist_offset(self, channel: Any, next_offset: int) -> None:
        """Update the channel's ``last_message_id`` via the handler.

        Delegates to :meth:`ChannelHandler.update_last_message_id` so
        the SQL lives in one place. The handler silently no-ops on a
        non-strictly-greater value, which is the desired behaviour
        here.
        """
        if next_offset <= channel.last_message_id:
            return
        try:
            self._handler.update_last_message_id(
                channel.channel_id, next_offset
            )
        except Exception as exc:  # noqa: BLE001 -- persistence is best-effort
            logger.debug(
                "ChannelMonitorService: failed to persist last_message_id for %s: %s",
                channel.channel_id,
                exc,
            )


def create_channel_monitor_service(
    *,
    transport: TelegramTransportPort,
    handler: ChannelHandler,
    notifier: NotificationPort,
    tick_interval: float = DEFAULT_TICK_INTERVAL,
    sleep_fn: Callable[[float], None] | None = None,
    chat_id: int | None = None,
) -> ChannelMonitorService:
    """Factory for :class:`ChannelMonitorService`."""
    return ChannelMonitorService(
        transport=transport,
        handler=handler,
        notifier=notifier,
        tick_interval=tick_interval,
        sleep_fn=sleep_fn,
        chat_id=chat_id,
    )
