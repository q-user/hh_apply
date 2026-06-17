"""JobHandler -- atomic claim / lock / mark of ``apply_jobs`` rows.

This is the slice's local facade over :class:`ApplyJobsRepository`. It
returns :class:`ApplyJob` DTOs (not raw ``ApplyJobModel``) so the rest
of the slice doesn't need to depend on the storage layer.

The actual claim SQL lives in the legacy repository (no
re-implementation); this handler is a thin adapter that:
  * injects the slice's clock (for deterministic tests),
  * projects :class:`ApplyJobModel` → :class:`ApplyJob`,
  * exposes ``mark_succeeded`` / ``mark_failed`` / ``mark_retry``
    as plain SQL operations on the shared connection.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from job_bot.application_submit.models.apply_job import ApplyJob

if TYPE_CHECKING:
    from job_bot._legacy_compat.storage.models.application_draft import (
        ApplicationDraftModel,
    )

logger = logging.getLogger(__package__)

# Залипший lock старше этого — подбираем (предыдущий воркер умер).
LOCK_TIMEOUT_SECONDS = 30 * 60


class _ClockPort(Protocol):
    def now(self) -> datetime: ...


class JobHandler:
    """Claim / lock / mark operations for the apply-worker queue."""

    def __init__(
        self,
        storage_conn: sqlite3.Connection,
        *,
        clock: "_ClockPort | None" = None,
    ) -> None:
        self._conn = storage_conn
        # Lazy import: the slice should not fail to import if the legacy
        # package isn't fully initialised in some test environments.
        from job_bot._legacy_compat.storage import StorageFacade

        self._facade = StorageFacade(storage_conn)
        # Optional clock for deterministic tests; defaults to real time.
        self._clock = clock

    # ─── Claim / get ───────────────────────────────────────────

    def claim_next(self, worker_id: str) -> ApplyJob | None:
        """Atomic claim-and-lock of the next runnable job.

        Delegates to :meth:`ApplyJobsRepository.claim_next_job` (SELECT +
        UPDATE to running) followed by :meth:`ApplyJobsRepository.lock_job`
        (increments ``attempts``), so the SQL lives in exactly one place
        and matches the legacy ``ApplyWorkerService._claim_next_job``
        semantics. Returns a slice-local :class:`ApplyJob` projected from
        the locked row.
        """
        now = self._now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        cutoff_str = (now - timedelta(seconds=LOCK_TIMEOUT_SECONDS)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        model = self._facade.apply_jobs.claim_next_job(
            worker_id=worker_id,
            now_str=now_str,
            cutoff_str=cutoff_str,
        )
        if model is None:
            return None
        # Increment attempts in the same transaction (issue #44).
        locked_at_iso = now.isoformat()
        self._facade.apply_jobs.lock_job(
            job_id=model.id or 0,
            worker_id=worker_id,
            locked_at=locked_at_iso,
        )
        # ``claim_next_job`` returns the row that was selected (not the
        # post-update state). Re-fetch via :meth:`get` to get a fresh
        # view of ``locked_at``/``locked_by``/``attempts`` after the
        # UPDATEs in the repository.
        self._facade.apply_jobs.commit()
        assert model.id is not None
        return self.get(model.id)

    def _now(self) -> datetime:
        """Return the current time (clock-aware, defaults to system time)."""
        if self._clock is not None:
            value = self._clock.now()
            # Some clocks return tz-aware datetimes; we normalise to naive
            # so the strftime-based comparisons are consistent.
            return value.replace(tzinfo=None) if value.tzinfo else value
        return datetime.now()

    def get(self, job_id: int) -> ApplyJob | None:
        """Fetch a job by id and project to :class:`ApplyJob`."""
        cur = self._conn.execute(
            "SELECT * FROM apply_jobs WHERE id = ?", (job_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ApplyJob.from_dict(dict(row))

    # ─── Mark ──────────────────────────────────────────────────

    def mark_succeeded(self, job_id: int) -> None:
        """Mark the job as ``succeeded`` (terminal) and clear the lock."""
        self._conn.execute(
            """
            UPDATE apply_jobs
            SET status = 'succeeded',
                last_error = NULL,
                locked_at = NULL,
                locked_by = NULL
            WHERE id = ?;
            """,
            (job_id,),
        )
        self.commit()

    def mark_failed(self, job_id: int, error: str) -> None:
        """Mark the job as ``failed`` (terminal) with ``last_error``."""
        self._conn.execute(
            """
            UPDATE apply_jobs
            SET status = 'failed',
                last_error = ?,
                locked_at = NULL,
                locked_by = NULL
            WHERE id = ?;
            """,
            (error, job_id),
        )
        self.commit()

    def mark_retry(self, job_id: int, error: str, next_attempt_at: str) -> None:
        """Reset the job to ``queued`` for a future retry."""
        self._conn.execute(
            """
            UPDATE apply_jobs
            SET status = 'queued',
                last_error = ?,
                next_attempt_at = ?,
                locked_at = NULL,
                locked_by = NULL
            WHERE id = ?;
            """,
            (error, next_attempt_at, job_id),
        )
        self.commit()

    # ─── Drafts ────────────────────────────────────────────────

    def load_draft(self, draft_id: int) -> "ApplicationDraftModel | None":
        """Fetch the application_draft row referenced by the job."""
        from typing import cast

        result = self._facade.application_drafts.get(draft_id)
        if result is None:
            return None
        return cast("ApplicationDraftModel", result)

    def save_draft(self, draft: "ApplicationDraftModel") -> None:
        """Persist a draft (e.g. when changing ``status`` to ``applied``)."""
        self._facade.application_drafts.save(draft)

    # ─── Commit ────────────────────────────────────────────────

    def commit(self) -> None:
        """Commit pending writes (no-op-safe outside a transaction)."""
        if self._conn.in_transaction:
            self._conn.commit()


__all__ = ["JobHandler", "LOCK_TIMEOUT_SECONDS"]
