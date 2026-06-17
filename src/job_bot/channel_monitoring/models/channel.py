"""Channel domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4


@dataclass
class Channel:
    """A monitored Telegram channel."""

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    channel_id: str = ""
    enabled: bool = True
    last_message_id: int = 0
    filter_keywords: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ChannelCreate:
    """Data for creating a new channel."""

    name: str
    channel_id: str
    enabled: bool = True
    filter_keywords: list[str] = field(default_factory=list)

    def to_channel(self) -> Channel:
        """Convert to a Channel entity."""
        return Channel(
            name=self.name,
            channel_id=self.channel_id,
            enabled=self.enabled,
            filter_keywords=list(self.filter_keywords),
        )
