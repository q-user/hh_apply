"""WorkerService -- the slice's main worker loop.

Encapsulates the apply-worker loop using the slice's :class:`JobPort`
and :class:`ApplyOnePort` instead of the legacy ``ApplyWorkerService``.
Behaviour mirrors the legacy worker: 5/15/60 min backoff, max 5
attempts, give up on fatal / max attempts, notify on success/failure.

The actual HTTP submission is delegated to :class:`ApplyOneHandler`
(→ :func:`hh_applicant_tool.services.apply_one.make_default_apply_one`)
and the test pipeline is delegated to :class:`TestHandler`
(→ :class:`hh_applicant_tool.services.vacancy_tests.VacancyTestsService`).
The slice does not reimplement the legacy services.
"""

from __future__ import annotations

import logging
import socket
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from job_bot.application_submit.handlers.retry_handler import (
    DEFAULT_MAX_ATTEMPTS,
    RetryHandler,
)
from job_bot.application_submit.models.submit_result import (
    SubmitResult,
    SubmitStatus,
)

logger = logging.getLogger(__package__)

DEFAULT_IDLE_SLEEP_SECONDS = 5.0

Notifier = Callable[[str, str], None]


class _ClockPort(Protocol):
    def now(self) -> datetime: ...


class _DelayPort(Protocol):
    def sleep(self, seconds: float) -> None: ...


# ─── Stats ─────────────────────────────────────────────────────────────


@dataclass
class RunStats:
    """Stats for one :meth:`WorkerService.run` invocation."""

    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0
    idle_loops: int = 0
    last_result: SubmitResult | None = None


# ─── Worker ────────────────────────────────────────────────────────────


class WorkerService:
    """Apply-worker loop using the slice's ports.

    Dependencies:
        * ``storage_conn`` -- raw ``sqlite3.Connection`` (the slice's
          :class:`JobHandler` will create its own ``StorageFacade``).
        * ``apply_one`` -- :class:`ApplyOnePort` (an apply-one callable).
        * ``retry`` -- :class:`RetryHandler` (backoff / give-up policy).
        * ``notifier`` -- ``Callable[[str, str], None]`` for success /
          failure notifications. ``None`` disables notifications.
    """

    def __init__(
        self,
        storage_conn: sqlite3.Connection,
        apply_one: Any,
        retry: RetryHandler | None = None,
        *,
        notifier: Notifier | None = None,
        clock: _ClockPort | None = None,
        delay: _DelayPort | None = None,
        worker_id: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        idle_sleep_seconds: float = DEFAULT_IDLE_SLEEP_SECONDS,
    ) -> None:
        from job_bot.application_submit.handlers.job_handler import JobHandler

        self._storage_conn = storage_conn
        self._jobs = JobHandler(storage_conn)
        self._apply_one = apply_one
        self._retry = retry or RetryHandler()
        self._notifier = notifier
        self._clock = clock or _default_clock()
        self._delay = delay or _default_delay()
        self._worker_id = worker_id or _default_worker_id()
        self._max_attempts = max_attempts
        self._idle_sleep_seconds = idle_sleep_seconds
        self._stop_requested = False

    # ─── Public properties ──────────────────────────────────────

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def clock(self) -> _ClockPort:
        return self._clock

    @property
    def idle_sleep_seconds(self) -> float:
        return self._idle_sleep_seconds

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def jobs(self) -> Any:
        """The underlying :class:`JobHandler` (for tests / introspection)."""
        return self._jobs

    def stop(self) -> None:
        """Request the worker to stop after the current iteration."""
        self._stop_requested = True

    # ─── Run loop ───────────────────────────────────────────────

    def process_one(self) -> SubmitResult | None:
        """Run one tick of the worker.

        Returns:
            :class:`SubmitResult` describing the outcome, or ``None`` if
            the queue was empty.
        """
        job = self._jobs.claim_next(worker_id=self._worker_id)
        if job is None:
            return None
        if job.id is None:
            return None
        return self._process_claimed_job(job)

    def run(
        self,
        *,
        max_jobs: int | None = None,
        stop_when_idle: bool = False,
    ) -> RunStats:
        """Run the worker loop until stopped / idle / max-jobs reached."""
        self._stop_requested = False
        stats = RunStats()
        if max_jobs is not None:
            stop_when_idle = True

        try:
            while not self._stop_requested:
                if max_jobs is not None and stats.processed >= max_jobs:
                    break
                try:
                    result = self.process_one()
                except Exception as ex:  # noqa: BLE001
                    logger.exception("apply-worker: unexpected error: %s", ex)
                    self._delay.sleep(self._idle_sleep_seconds)
                    continue

                if result is None:
                    stats.idle_loops += 1
                    if stop_when_idle:
                        break
                    self._delay.sleep(self._idle_sleep_seconds)
                    continue

                stats.processed += 1
                stats.last_result = result
                if result.status == SubmitStatus.SUCCEEDED:
                    stats.succeeded += 1
                elif result.status == SubmitStatus.FAILED:
                    stats.failed += 1
                elif result.status == SubmitStatus.SKIPPED:
                    stats.retried += 1
        except KeyboardInterrupt:
            logger.info("apply-worker: SIGINT, exiting gracefully")
            self._stop_requested = True

        return stats

    # ─── Internals ──────────────────────────────────────────────

    def _process_claimed_job(self, job: Any) -> SubmitResult:
        """Apply the job and update statuses."""
        # attempts has already been incremented by the claim SQL.
        draft = self._jobs.load_draft(job.draft_id)
        if draft is None:
            error = f"application_draft id={job.draft_id} не найден"
            self._jobs.mark_failed(job.id, error)
            self._notify_failure(draft=None, error=error)
            return SubmitResult(
                status=SubmitStatus.FAILED,
                job_id=job.id or 0,
                draft_id=job.draft_id,
                attempts=job.attempts,
                last_error=error,
            )

        # Lazy import to avoid a hard dependency on the legacy service.
        from hh_applicant_tool.services.apply_worker import (
            FatalError,
            RetryableError,
        )

        draft.status = "applying"
        self._jobs.save_draft(draft)
        self._jobs.commit()

        try:
            self._apply_one(draft)
        except FatalError as ex:
            logger.error("apply-worker: FatalError job=%d: %s", job.id, ex)
            return self._mark_failed(job, draft, str(ex))
        except RetryableError as ex:
            logger.warning(
                "apply-worker: RetryableError job=%d attempt=%d: %s",
                job.id,
                job.attempts,
                ex,
            )
            return self._handle_retryable(job, draft, str(ex))
        except Exception as ex:  # noqa: BLE001
            # Unknown error -> retryable (conservative).
            logger.exception(
                "apply-worker: unexpected error job=%d: %s", job.id, ex
            )
            return self._handle_retryable(job, draft, f"unexpected: {ex!r}")
        else:
            return self._mark_succeeded(job, draft)

    def _mark_succeeded(self, job: Any, draft: Any) -> SubmitResult:
        self._jobs.mark_succeeded(job.id)
        draft.status = "applied"
        draft.last_error = None
        if draft.hh_response_url is None:
            draft.hh_response_url = f"https://hh.ru/vacancy/{draft.vacancy_id}"
        self._jobs.save_draft(draft)
        self._jobs.commit()
        self._notify_success(draft=draft)
        return SubmitResult(
            status=SubmitStatus.SUCCEEDED,
            job_id=job.id or 0,
            draft_id=draft.id or 0,
            attempts=job.attempts,
        )

    def _mark_failed(
        self, job: Any, draft: Any | None, error: str
    ) -> SubmitResult:
        self._jobs.mark_failed(job.id, error)
        if draft is not None:
            draft.status = "failed"
            draft.last_error = error
            self._jobs.save_draft(draft)
            self._jobs.commit()
        self._notify_failure(draft=draft, error=error)
        return SubmitResult(
            status=SubmitStatus.FAILED,
            job_id=job.id or 0,
            draft_id=draft.id if draft is not None else 0,
            attempts=job.attempts,
            last_error=error,
        )

    def _handle_retryable(
        self, job: Any, draft: Any, error: str
    ) -> SubmitResult:
        """Schedule a retry (or give up if max attempts reached)."""
        if not self._retry.should_retry(job.attempts, self._max_attempts):
            logger.warning(
                "apply-worker: max_attempts=%d reached (job=%d), giving up",
                self._max_attempts,
                job.id,
            )
            return self._mark_failed(job, draft, error)

        next_at = self._retry.next_attempt_at(job.attempts, self._clock.now())
        self._jobs.mark_retry(job.id, error=error, next_attempt_at=next_at)
        # Draft stays in "applying" until the next attempt.
        return SubmitResult(
            status=SubmitStatus.SKIPPED,
            job_id=job.id or 0,
            draft_id=draft.id or 0,
            attempts=job.attempts,
            last_error=error,
        )

    # ─── Notifications ──────────────────────────────────────────

    def _notify(
        self,
        kind: str,
        draft: Any | None,
        error: str,
    ) -> None:
        """Render the notification text and call the notifier (if any).

        The notifier contract is ``Callable[[str, str], None]``: the
        first arg is ``kind`` (``"success"``/``"failure"``), the second
        is the human-readable text. Errors raised by the notifier are
        logged and swallowed -- a broken notifier must never crash the
        worker loop.
        """
        if self._notifier is None:
            return
        text = (
            _render_vacancy_text(kind, draft, error)
            if draft is not None
            else f"❌ Не удалось отправить отклик:\nПричина: {error}"
        )
        try:
            self._notifier(kind, text)
        except Exception:  # noqa: BLE001
            logger.exception("apply-worker: notifier raised")

    def _notify_success(self, draft: Any) -> None:
        self._notify("success", draft, "")

    def _notify_failure(self, draft: Any | None, error: str) -> None:
        self._notify("failure", draft, error)


# ─── Defaults / rendering helpers ──────────────────────────────────────


def _default_clock() -> _ClockPort:
    from hh_applicant_tool.infrastructure.time import SystemClock

    return SystemClock()


def _default_delay() -> _DelayPort:
    from hh_applicant_tool.infrastructure.delay import TimeDelay

    return TimeDelay()


def _default_worker_id() -> str:
    try:
        host = socket.gethostname()
    except OSError:
        host = "worker"
    return f"{host}:{uuid.uuid4().hex[:8]}"


def _render_vacancy_text(kind: str, draft: Any, error: str) -> str:
    """Render a human-friendly notification line for a draft."""
    vacancy = (
        draft.full_vacancy_json
        if getattr(draft, "full_vacancy_json", None)
        else {}
    )
    name = str(vacancy.get("name") or f"vacancy #{draft.vacancy_id}")
    employer_obj = vacancy.get("employer") or {}
    if isinstance(employer_obj, dict):
        employer = employer_obj.get("name") or "(без названия)"
    else:
        employer = "(без названия)"
    url = str(
        vacancy.get("alternate_url")
        or f"https://hh.ru/vacancy/{draft.vacancy_id}"
    )
    if kind == "success":
        return f"✅ Отклик отправлен:\n{name} — {employer}\n{url}"
    return (
        f"❌ Не удалось отправить отклик:\n"
        f"{name} — {employer}\nПричина: {error}"
    )


__all__ = [
    "WorkerService",
    "RunStats",
    "DEFAULT_IDLE_SLEEP_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
]
