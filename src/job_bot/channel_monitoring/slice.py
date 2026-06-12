"""``ChannelMonitorSlice`` -- entry point and factory (issue #61).

The slice now wires the :class:`ChannelPoller` and
:class:`ChannelMonitorService` on top of the existing
:class:`ChannelHandler`. Callers (CLI, scheduler) drive the service via
:pyattr:`ChannelMonitorSlice.service` (the orchestrator) or
:pyattr:`ChannelMonitorSlice.poller` (per-channel poll helper).
"""

from __future__ import annotations

from typing import Any

from job_bot.channel_monitoring.handlers.channel_handler import ChannelHandler
from job_bot.channel_monitoring.ports.channel_port import ChannelPort
from job_bot.channel_monitoring.ports.notification_port import (
    NotificationPort,
    NullNotificationPort,
)
from job_bot.channel_monitoring.services.channel_poller import ChannelPoller
from job_bot.channel_monitoring.services.monitor_service import (
    DEFAULT_TICK_INTERVAL,
    ChannelMonitorService,
    create_channel_monitor_service,
)


class ChannelMonitorSlice:
    """Channel monitoring slice (issue #61).

    Aggregates the :class:`ChannelHandler` (CRUD + dedup),
    :class:`ChannelPoller` (per-channel poll) and
    :class:`ChannelMonitorService` (orchestrator). The notification
    port is injectable so tests can pass the no-op adapter and the
    CLI can pass a real Telegram/MAX transport adapter.
    """

    def __init__(
        self,
        conn: Any,
        *,
        notifier: NotificationPort | None = None,
        tick_interval: float = DEFAULT_TICK_INTERVAL,
        transport: Any = None,
        chat_id: int | None = None,
    ) -> None:
        self._conn = conn
        self._handler = ChannelHandler(conn)
        self._notifier: NotificationPort = notifier or NullNotificationPort()
        self._transport = transport
        self._tick_interval = tick_interval
        self._chat_id = chat_id
        self._service: ChannelMonitorService | None = None

    @property
    def channels(self) -> ChannelPort:
        """The channel CRUD + dedup port (delegates to the handler)."""
        return self._handler

    @property
    def handler(self) -> ChannelHandler:
        """The underlying :class:`ChannelHandler`."""
        return self._handler

    @property
    def notifier(self) -> NotificationPort:
        """The notification port (Telegram / MAX / no-op)."""
        return self._notifier

    @property
    def service(self) -> ChannelMonitorService:
        """The :class:`ChannelMonitorService` orchestrator (lazy-built).

        The service is constructed on first access so callers that
        only need the handler (e.g., read-only tests) don't have to
        pass a transport.
        """
        if self._service is None:
            if self._transport is None:
                raise RuntimeError(
                    "ChannelMonitorSlice.service requires a transport; "
                    "pass one via the `transport=` constructor arg"
                )
            self._service = create_channel_monitor_service(
                transport=self._transport,
                handler=self._handler,
                notifier=self._notifier,
                tick_interval=self._tick_interval,
                chat_id=self._chat_id,
            )
        return self._service

    def make_poller(self, *, channel: Any) -> ChannelPoller:
        """Build a :class:`ChannelPoller` for a specific channel.

        Useful for callers that want fine-grained control over the
        offset (e.g., a one-off backfill job).
        """
        if self._transport is None:
            raise RuntimeError(
                "ChannelMonitorSlice.make_poller requires a transport"
            )
        return ChannelPoller(
            transport=self._transport,
            channel=channel,
            handler=self._handler,
        )


def create_channel_monitor_slice(
    conn: Any,
    *,
    notifier: NotificationPort | None = None,
    tick_interval: float = DEFAULT_TICK_INTERVAL,
    transport: Any = None,
    chat_id: int | None = None,
) -> ChannelMonitorSlice:
    """Factory for :class:`ChannelMonitorSlice`."""
    return ChannelMonitorSlice(
        conn=conn,
        notifier=notifier,
        tick_interval=tick_interval,
        transport=transport,
        chat_id=chat_id,
    )
