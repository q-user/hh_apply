"""ApplyJob DTO and status enum for the application_submit slice.

A slice-local dataclass that mirrors the storage model's fields the
worker actually needs. Keeps the rest of the slice free of direct
dependency on the storage layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar


class ApplyJobStatus:
    """Apply-job lifecycle status (string constants).

    Values match the underlying ``apply_jobs.status`` column exactly
    so they can be persisted without translation.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class ApplyJob:
    """Slice-local view of an ``apply_jobs`` row."""

    draft_id: int = 0
    id: int | None = None
    status: str = ApplyJobStatus.QUEUED
    attempts: int = 0
    max_attempts: int = 5
    next_attempt_at: str | None = None
    locked_at: str | None = None
    locked_by: str | None = None
    last_error: str | None = None
    chat_id: int | None = None

    TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {ApplyJobStatus.SUCCEEDED, ApplyJobStatus.FAILED}
    )

    def is_terminal(self) -> bool:
        """``True`` for ``succeeded`` / ``failed`` (no further processing)."""
        return self.status in self.TERMINAL_STATUSES

    def is_locked(self) -> bool:
        """``True`` when the job is currently held by a worker."""
        return (
            self.status == ApplyJobStatus.RUNNING and self.locked_by is not None
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApplyJob":
        """Build an :class:`ApplyJob` from a raw ``sqlite3.Row`` mapping."""
        return cls(
            id=data.get("id"),
            draft_id=data["draft_id"],
            status=data.get("status") or ApplyJobStatus.QUEUED,
            attempts=int(data.get("attempts") or 0),
            max_attempts=int(data.get("max_attempts") or 5),
            next_attempt_at=data.get("next_attempt_at"),
            locked_at=data.get("locked_at"),
            locked_by=data.get("locked_by"),
            last_error=data.get("last_error"),
            chat_id=data.get("chat_id"),
        )


__all__ = ["ApplyJob", "ApplyJobStatus"]
