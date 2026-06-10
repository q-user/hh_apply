"""Vacancy link domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class VacancyLink:
    """A vacancy link extracted from a channel message."""

    url: str
    vacancy_id: str
    source_channel: str
    message_id: int
    created_at: datetime = field(default_factory=datetime.now)
