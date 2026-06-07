from __future__ import annotations

from .base import BaseModel


class ApplyJobModel(BaseModel):
    """Задача асинхронной отправки черновика.

    UNIQUE(draft_id) гарантирует один активный job на черновик. Retry-логика
    опирается на (attempts, max_attempts, next_attempt_at); блокировка — на
    (locked_at, locked_by).
    """

    id: int | None = None
    draft_id: int
    status: str = "queued"
    attempts: int = 0
    max_attempts: int = 3
    next_attempt_at: str | None = None
    locked_at: str | None = None
    locked_by: str | None = None
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
