"""Channel monitoring services (issue #61).

Public surface::

    from job_bot.channel_monitoring.services import (
        ChannelPoller,
        ChannelMonitorService,
        DEFAULT_TICK_INTERVAL,
        create_channel_monitor_service,
    )
"""

from __future__ import annotations

from job_bot.channel_monitoring.services.channel_poller import ChannelPoller
from job_bot.channel_monitoring.services.monitor_service import (
    DEFAULT_TICK_INTERVAL,
    ChannelMonitorService,
    create_channel_monitor_service,
)

__all__ = [
    "ChannelPoller",
    "ChannelMonitorService",
    "DEFAULT_TICK_INTERVAL",
    "create_channel_monitor_service",
]
