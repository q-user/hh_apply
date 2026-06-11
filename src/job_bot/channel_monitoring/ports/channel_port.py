"""Port for channel monitoring operations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from job_bot.channel_monitoring.models.channel import Channel, ChannelCreate
from job_bot.channel_monitoring.models.vacancy_link import VacancyLink


@runtime_checkable
class ChannelPort(Protocol):
    """Port exposing channel-monitoring operations to other slices."""

    def add_channel(self, channel: ChannelCreate) -> Channel: ...

    def remove_channel(self, channel_id: str) -> bool: ...

    def list_channels(self, enabled_only: bool = False) -> list[Channel]: ...

    def get_channel(self, channel_id: str) -> Channel | None: ...

    def parse_message(
        self, text: str, source_channel: str, message_id: int
    ) -> list[VacancyLink]: ...

    def is_already_processed(self, vacancy_id: str) -> bool: ...
