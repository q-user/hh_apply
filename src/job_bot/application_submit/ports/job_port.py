"""JobPort -- interface for apply_jobs operations used by the slice."""

from __future__ import annotations

from typing import Any, Protocol

from job_bot.application_submit.models.apply_job import ApplyJob


class JobPort(Protocol):
    """Atomic claim / lock / mark of ``apply_jobs`` rows.

    Implemented by :class:`job_bot.application_submit.handlers.job_handler.JobHandler`.
    """

    def claim_next(self, worker_id: str) -> ApplyJob | None:
        """Atomically claim and lock the next runnable job for ``worker_id``.

        Returns:
            The claimed :class:`ApplyJob` (with ``status=running`` and
            ``locked_by=worker_id``) or ``None`` when the queue is empty.
        """
        ...

    def get(self, job_id: int) -> ApplyJob | None:
        """Fetch a job by id."""
        ...

    def mark_succeeded(self, job_id: int) -> None:
        """Mark the job as ``succeeded`` (and clear the lock)."""
        ...

    def mark_failed(self, job_id: int, error: str) -> None:
        """Mark the job as ``failed`` (terminal) with ``last_error``."""
        ...

    def mark_retry(self, job_id: int, error: str, next_attempt_at: str) -> None:
        """Mark the job as queued again with a new ``next_attempt_at``."""
        ...

    def load_draft(self, draft_id: int) -> Any | None:
        """Fetch the application_draft row referenced by the job."""
        ...

    def save_draft(self, draft: Any) -> None:
        """Persist a draft (e.g. when changing ``status`` to ``applied``)."""
        ...

    def commit(self) -> None:
        """Commit pending writes (no-op-safe)."""
        ...


__all__ = ["JobPort"]
