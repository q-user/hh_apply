"""Channel Monitoring slice (issue #61).

Public API::

    from job_bot.channel_monitoring import (
        Channel,
        ChannelCreate,
        ChannelHandler,
        ChannelMonitorSlice,
        ChannelPort,
        ChannelPoller,
        ChannelMonitorService,
        NotificationPort,
        NullNotificationPort,
        VacancyLink,
        create_channel_monitor_slice,
        create_channel_monitor_service,
        create_null_notification_port,
    )
"""

from __future__ import annotations

from job_bot.channel_monitoring.handlers.channel_handler import ChannelHandler
from job_bot.channel_monitoring.models.channel import Channel, ChannelCreate
from job_bot.channel_monitoring.models.vacancy_link import VacancyLink
from job_bot.channel_monitoring.ports.channel_port import ChannelPort
from job_bot.channel_monitoring.ports.notification_port import (
    NotificationPort,
    NullNotificationPort,
    create_null_notification_port,
)
from job_bot.channel_monitoring.services.channel_poller import ChannelPoller
from job_bot.channel_monitoring.services.monitor_service import (
    ChannelMonitorService,
    create_channel_monitor_service,
)
from job_bot.channel_monitoring.slice import (
    ChannelMonitorSlice,
    create_channel_monitor_slice,
)

__all__ = [
    "Channel",
    "ChannelCreate",
    "ChannelHandler",
    "ChannelMonitorService",
    "ChannelMonitorSlice",
    "ChannelPort",
    "ChannelPoller",
    "NotificationPort",
    "NullNotificationPort",
    "VacancyLink",
    "create_channel_monitor_service",
    "create_channel_monitor_slice",
    "create_null_notification_port",
]
