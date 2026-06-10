"""Channel Monitoring slice - entry point and factory."""

from __future__ import annotations

from typing import Any

from job_bot.channel_monitoring.handlers.channel_handler import ChannelHandler
from job_bot.channel_monitoring.ports.channel_port import ChannelPort


class ChannelMonitorSlice:
    """Channel monitoring slice - encapsulates channel + vacancy-link logic."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._handler = ChannelHandler(conn)

    @property
    def channels(self) -> ChannelPort:
        """Get the channel-monitoring port."""
        return self._handler

    @property
    def handler(self) -> ChannelHandler:
        """Get the underlying channel handler."""
        return self._handler


def create_channel_monitor_slice(conn: Any) -> ChannelMonitorSlice:
    """Factory function to create a :class:`ChannelMonitorSlice`."""
    return ChannelMonitorSlice(conn)
