from __future__ import annotations

from datetime import datetime

from hh_applicant_tool.storage.models.base import (  # noqa: F401
    BaseModel,
    mapped,
)  # BaseModel re-exported for callers
from job_bot.shared.utils.datetime_utils import try_parse_datetime


class ResumeModel(BaseModel):
    id: str
    title: str = mapped(transform=lambda x: x or "Без названия", default="")
    url: str = ""
    alternate_url: str = ""
    status_id: str | None = mapped(path="status.id", default=None)
    status_name: str | None = mapped(path="status.name", default=None)
    can_publish_or_update: bool = False
    total_views: int = mapped(path="counters.total_views", default=0)
    new_views: int = mapped(path="counters.new_views", default=0)
    created_at: datetime | None = mapped(
        transform=try_parse_datetime, default=None
    )
    updated_at: datetime | None = mapped(
        transform=try_parse_datetime, default=None
    )
