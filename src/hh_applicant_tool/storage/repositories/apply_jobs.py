from __future__ import annotations

from ..models.apply_job import ApplyJobModel
from .base import BaseRepository


class ApplyJobsRepository(BaseRepository):
    __table__ = "apply_jobs"
    model = ApplyJobModel
    # Один job на черновик: повторное одобрение не плодит дубликаты в очереди.
    conflict_columns = ("draft_id",)
