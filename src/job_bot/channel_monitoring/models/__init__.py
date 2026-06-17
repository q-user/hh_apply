"""Domain models for the channel_monitoring slice."""

from .channel import Channel, ChannelCreate
from .vacancy_link import VacancyLink

__all__ = ["Channel", "ChannelCreate", "VacancyLink"]
