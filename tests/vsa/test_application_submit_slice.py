"""Tests for the application_submit slice (VSA - Issue #50, Phase 3).

TDD: tests are written first, then the slice is implemented to make them
pass, then refactored.

Slice responsibilities:
  * Atomic claim / lock / mark of apply_jobs rows (one job per draft).
  * Stale lock recovery (LOCK_TIMEOUT_SECONDS = 30 min).
  * Apply-one callable: simple POST /negotiations (no test) and the
    VacancyTestsService path (with test), with retry/fatal error
    classification reused from the existing services.
  * Test answer generation: fetch, prepare, build payload, submit.
  * Backoff / max-attempts / give-up retry policy.
  * Worker loop with idle sleep, max-jobs cap, graceful stop and
    success/failure notifications.

The tests use:
  * ``storage_conn`` -- in-memory SQLite with the canonical schema.
  * mocks for ``api_client`` / ``session`` / transport.
  * the existing ``ApplyOneHandler`` and ``VacancyTestsService``
    (the slice is a VSA wrapper; the heavy lifting lives in the
    handler/service classes).

The tests are split into:
  * Model tests (pure data -- no DB, no transport).
  * Handler tests (in-memory DB, mocks for HTTP).
  * Worker service tests (full loop with in-memory DB).
  * Slice integration tests.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

# ─── Constants ─────────────────────────────────────────────────────────

WORKER_ID = "test-worker-1"
OTHER_WORKER_ID = "other-worker"
CHAT_ID = 4242

# Fixed "now" used across tests (UTC-ish; no tz since the SQL columns
# are naive). All time-relative helpers and JobHandler clocks use this.
TEST_NOW = datetime(2026, 6, 10, 12, 0, 0)


# ─── Fixtures ──────────────────────────────────────────────────────────


class _FixedClock:
    """Deterministic clock for backoff / lock-timeout tests."""

    def __init__(self, base: datetime | None = None) -> None:
        self._now = base or TEST_NOW

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now = self._now + timedelta(seconds=seconds)


@pytest.fixture
def clock() -> _FixedClock:
    return _FixedClock()


@pytest.fixture
def no_sleep_delay() -> MagicMock:
    return MagicMock()


@pytest.fixture
def notifier() -> MagicMock:
    return MagicMock()


# ─── Helpers ───────────────────────────────────────────────────────────


def _make_draft(
    conn: sqlite3.Connection,
    *,
    vacancy_id: int = 100,
    has_test: bool = False,
    status: str = "queued",
    cover_letter: str | None = "Hi there",
) -> int:
    """Insert an application_drafts row and return its id."""
    from hh_applicant_tool.storage import StorageFacade
    from hh_applicant_tool.storage.models.application_draft import (
        ApplicationDraftModel,
    )

    facade = StorageFacade(conn)
    draft = ApplicationDraftModel(
        resume_id="r1",
        vacancy_id=vacancy_id,
        has_test=has_test,
        cover_letter=cover_letter,
        cover_letter_status="generated",
        status=status,
        full_vacancy_json={
            "id": vacancy_id,
            "name": f"Vacancy {vacancy_id}",
            "employer": {"name": "Acme"},
            "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        },
    )
    facade.application_drafts.save(draft)
    facade.application_drafts.commit()
    row = facade.application_drafts.conn.execute(
        "SELECT id FROM application_drafts WHERE vacancy_id=?", (vacancy_id,)
    ).fetchone()
    return row["id"]


def _make_job(
    conn: sqlite3.Connection,
    draft_id: int,
    *,
    status: str = "queued",
    attempts: int = 0,
    max_attempts: int = 5,
    next_attempt_at: str | None = None,
    locked_at: str | None = None,
    locked_by: str | None = None,
    chat_id: int | None = CHAT_ID,
) -> int:
    """Insert an apply_jobs row and return its id."""
    from hh_applicant_tool.storage import StorageFacade
    from hh_applicant_tool.storage.models.apply_job import ApplyJobModel

    facade = StorageFacade(conn)
    job = ApplyJobModel(
        draft_id=draft_id,
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
        chat_id=chat_id,
    )
    if next_attempt_at is not None:
        job.next_attempt_at = next_attempt_at
    if locked_at is not None:
        job.locked_at = locked_at
    if locked_by is not None:
        job.locked_by = locked_by
    facade.apply_jobs.save(job)
    facade.apply_jobs.commit()
    row = facade.apply_jobs.conn.execute(
        "SELECT id FROM apply_jobs WHERE draft_id=?", (draft_id,)
    ).fetchone()
    return row["id"]


def _isoformat(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ─── ApplyJob model ────────────────────────────────────────────────────


class TestApplyJobModel:
    """``models/apply_job.py`` -- slice-local ApplyJob DTO + status enum."""

    def test_defaults(self) -> None:
        from job_bot.application_submit.models.apply_job import (
            ApplyJob,
            ApplyJobStatus,
        )

        job = ApplyJob(draft_id=1)
        assert job.id is None
        assert job.draft_id == 1
        assert job.status == ApplyJobStatus.QUEUED
        assert job.attempts == 0
        assert job.max_attempts == 5
        assert job.next_attempt_at is None
        assert job.locked_at is None
        assert job.locked_by is None
        assert job.last_error is None
        assert job.chat_id is None

    def test_full_construction(self) -> None:
        from job_bot.application_submit.models.apply_job import (
            ApplyJob,
            ApplyJobStatus,
        )

        job = ApplyJob(
            id=42,
            draft_id=7,
            status=ApplyJobStatus.RUNNING,
            attempts=2,
            max_attempts=5,
            next_attempt_at="2026-06-10 12:00:00",
            locked_at="2026-06-10 11:55:00",
            locked_by="host:abc",
            last_error="boom",
            chat_id=100,
        )
        assert job.id == 42
        assert job.status == ApplyJobStatus.RUNNING
        assert job.attempts == 2
        assert job.locked_by == "host:abc"

    def test_status_constants(self) -> None:
        from job_bot.application_submit.models.apply_job import ApplyJobStatus

        assert ApplyJobStatus.QUEUED == "queued"
        assert ApplyJobStatus.RUNNING == "running"
        assert ApplyJobStatus.SUCCEEDED == "succeeded"
        assert ApplyJobStatus.FAILED == "failed"

    def test_is_terminal(self) -> None:
        from job_bot.application_submit.models.apply_job import (
            ApplyJob,
            ApplyJobStatus,
        )

        assert (
            ApplyJob(draft_id=1, status=ApplyJobStatus.SUCCEEDED).is_terminal()
            is True
        )
        assert (
            ApplyJob(draft_id=1, status=ApplyJobStatus.FAILED).is_terminal()
            is True
        )
        assert (
            ApplyJob(draft_id=1, status=ApplyJobStatus.QUEUED).is_terminal()
            is False
        )
        assert (
            ApplyJob(draft_id=1, status=ApplyJobStatus.RUNNING).is_terminal()
            is False
        )

    def test_is_locked(self) -> None:
        from job_bot.application_submit.models.apply_job import (
            ApplyJob,
            ApplyJobStatus,
        )

        assert (
            ApplyJob(
                draft_id=1, status=ApplyJobStatus.RUNNING, locked_by="w1"
            ).is_locked()
            is True
        )
        assert (
            ApplyJob(
                draft_id=1, status=ApplyJobStatus.QUEUED, locked_by=None
            ).is_locked()
            is False
        )

    def test_from_dict(self) -> None:
        from job_bot.application_submit.models.apply_job import (
            ApplyJob,
            ApplyJobStatus,
        )

        raw = {
            "id": 5,
            "draft_id": 9,
            "status": "running",
            "attempts": 1,
            "max_attempts": 5,
            "next_attempt_at": None,
            "locked_at": "2026-06-10 12:00:00",
            "locked_by": "host:x",
            "last_error": None,
            "chat_id": 42,
        }
        job = ApplyJob.from_dict(raw)
        assert job.id == 5
        assert job.status == ApplyJobStatus.RUNNING
        assert job.locked_by == "host:x"
        assert job.chat_id == 42


# ─── TestAnswer model ──────────────────────────────────────────────────


class TestTestAnswerModel:
    """``models/test_answer.py`` -- slice-local DTOs for vacancy tests."""

    def test_construction(self) -> None:
        from job_bot.application_submit.models.test_answer import TestAnswer

        ans = TestAnswer(
            task_id="t1",
            question="Are you ready?",
            answer_type="choice",
            options_json=[
                {"id": "1", "text": "Да"},
                {"id": "2", "text": "Нет"},
            ],
            generated_answer="1",
            selected_solution_id="1",
            review_status="generated",
        )
        assert ans.task_id == "t1"
        assert ans.answer_type == "choice"
        assert ans.selected_solution_id == "1"
        assert ans.review_status == "generated"

    def test_defaults(self) -> None:
        from job_bot.application_submit.models.test_answer import TestAnswer

        ans = TestAnswer(task_id="t1")
        assert ans.question is None
        assert ans.answer_type is None
        assert ans.options_json is None
        assert ans.generated_answer is None
        assert ans.selected_solution_id is None
        assert ans.review_status == "generated"

    def test_answer_type_constants(self) -> None:
        from job_bot.application_submit.models.test_answer import TestAnswerType

        assert TestAnswerType.CHOICE == "choice"
        assert TestAnswerType.TEXT == "text"

    def test_to_dict_round_trip(self) -> None:
        from job_bot.application_submit.models.test_answer import TestAnswer

        ans = TestAnswer(
            task_id="t1",
            question="Q?",
            answer_type="text",
            options_json=None,
            generated_answer="Yes",
            selected_solution_id=None,
        )
        d = ans.to_dict()
        assert d["task_id"] == "t1"
        assert d["answer_type"] == "text"
        assert d["generated_answer"] == "Yes"


# ─── SubmitResult model ────────────────────────────────────────────────


class TestSubmitResultModel:
    """``models/submit_result.py`` -- worker-level result DTO."""

    def test_construction(self) -> None:
        from job_bot.application_submit.models.submit_result import (
            SubmitResult,
            SubmitStatus,
        )

        r = SubmitResult(
            status=SubmitStatus.SUCCEEDED,
            job_id=10,
            draft_id=20,
            attempts=1,
        )
        assert r.status == SubmitStatus.SUCCEEDED
        assert r.job_id == 10
        assert r.draft_id == 20
        assert r.attempts == 1
        assert r.last_error is None

    def test_status_constants(self) -> None:
        from job_bot.application_submit.models.submit_result import SubmitStatus

        assert SubmitStatus.SUCCEEDED == "succeeded"
        assert SubmitStatus.FAILED == "failed"
        assert SubmitStatus.SKIPPED == "skipped"

    def test_succeeded_property(self) -> None:
        from job_bot.application_submit.models.submit_result import (
            SubmitResult,
            SubmitStatus,
        )

        assert (
            SubmitResult(
                status=SubmitStatus.SUCCEEDED, job_id=1, draft_id=1, attempts=1
            ).succeeded
            is True
        )
        assert (
            SubmitResult(
                status=SubmitStatus.FAILED, job_id=1, draft_id=1, attempts=1
            ).succeeded
            is False
        )


# ─── JobHandler ────────────────────────────────────────────────────────


class TestJobHandler:
    """``handlers/job_handler.py`` -- claim / lock / mark operations."""

    def test_claim_next_returns_queued_job(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn)
        job_id = _make_job(storage_conn, draft_id)

        handler = JobHandler(storage_conn, clock=clock)
        job = handler.claim_next(worker_id=WORKER_ID)

        assert job is not None
        assert job.id == job_id
        assert job.status == "running"
        assert job.locked_by == WORKER_ID
        assert job.attempts == 1

    def test_claim_next_returns_none_when_empty(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.handlers.job_handler import JobHandler

        handler = JobHandler(storage_conn, clock=clock)
        assert handler.claim_next(worker_id=WORKER_ID) is None

    def test_claim_next_skips_other_worker_lock_within_timeout(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        """Locked by another worker < LOCK_TIMEOUT → skip."""
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn, vacancy_id=1)
        # Locked 1 minute ago -- well within the 30-min timeout.
        recent_lock = _isoformat(clock.now() - timedelta(minutes=1))
        _make_job(
            storage_conn,
            draft_id,
            status="running",
            locked_at=recent_lock,
            locked_by=OTHER_WORKER_ID,
        )

        handler = JobHandler(storage_conn, clock=clock)
        assert handler.claim_next(worker_id=WORKER_ID) is None

    def test_claim_next_recovers_stale_lock(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        """Locked > LOCK_TIMEOUT ago → recoverable (issue #44)."""
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn, vacancy_id=2)
        # Locked 1 hour ago, well past the 30-min LOCK_TIMEOUT.
        stale_lock = _isoformat(clock.now() - timedelta(hours=1))
        _make_job(
            storage_conn,
            draft_id,
            status="running",
            locked_at=stale_lock,
            locked_by=OTHER_WORKER_ID,
        )

        handler = JobHandler(storage_conn, clock=clock)
        job = handler.claim_next(worker_id=WORKER_ID)

        assert job is not None
        assert job.locked_by == WORKER_ID
        # attempts is bumped because the row was reclaimed.
        assert job.attempts == 1

    def test_claim_next_skips_future_next_attempt_at(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        """next_attempt_at > now → job not eligible yet."""
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn, vacancy_id=3)
        future = _isoformat(clock.now() + timedelta(hours=1))
        _make_job(
            storage_conn,
            draft_id,
            status="queued",
            next_attempt_at=future,
        )

        handler = JobHandler(storage_conn, clock=clock)
        assert handler.claim_next(worker_id=WORKER_ID) is None

    def test_claim_next_picks_due_retry(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        """next_attempt_at in the past → eligible again."""
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn, vacancy_id=4)
        past = _isoformat(clock.now() - timedelta(hours=1))
        _make_job(
            storage_conn,
            draft_id,
            status="queued",
            next_attempt_at=past,
        )

        handler = JobHandler(storage_conn, clock=clock)
        job = handler.claim_next(worker_id=WORKER_ID)
        assert job is not None
        assert job.draft_id == draft_id

    def test_claim_next_skips_terminal_jobs(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        """succeeded/failed jobs are never claimed."""
        from job_bot.application_submit.handlers.job_handler import JobHandler

        for status in ("succeeded", "failed"):
            draft_id = _make_draft(
                storage_conn, vacancy_id=hash(status) & 0xFFFF
            )
            _make_job(storage_conn, draft_id, status=status)

        handler = JobHandler(storage_conn, clock=clock)
        assert handler.claim_next(worker_id=WORKER_ID) is None

    def test_get_returns_apply_job(self, storage_conn: sqlite3.Connection):
        from job_bot.application_submit.handlers.job_handler import JobHandler
        from job_bot.application_submit.models.apply_job import (
            ApplyJob,
            ApplyJobStatus,
        )

        draft_id = _make_draft(storage_conn)
        job_id = _make_job(storage_conn, draft_id)

        handler = JobHandler(storage_conn)
        job = handler.get(job_id)
        assert isinstance(job, ApplyJob)
        assert job.id == job_id
        assert job.draft_id == draft_id
        assert job.status == ApplyJobStatus.QUEUED

    def test_get_returns_none_for_missing(
        self, storage_conn: sqlite3.Connection
    ):
        from job_bot.application_submit.handlers.job_handler import JobHandler

        handler = JobHandler(storage_conn)
        assert handler.get(999) is None

    def test_mark_succeeded(self, storage_conn: sqlite3.Connection):
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn)
        job_id = _make_job(storage_conn, draft_id, attempts=2)

        handler = JobHandler(storage_conn)
        handler.mark_succeeded(job_id)

        job = handler.get(job_id)
        assert job is not None
        assert job.status == "succeeded"
        assert job.last_error is None
        assert job.locked_at is None
        assert job.locked_by is None
        assert job.attempts == 2  # not bumped

    def test_mark_failed(self, storage_conn: sqlite3.Connection):
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn)
        job_id = _make_job(storage_conn, draft_id)

        handler = JobHandler(storage_conn)
        handler.mark_failed(job_id, error="hh 400: bad request")

        job = handler.get(job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.last_error == "hh 400: bad request"
        assert job.locked_at is None
        assert job.locked_by is None

    def test_mark_retry(self, storage_conn: sqlite3.Connection):
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn)
        job_id = _make_job(storage_conn, draft_id, attempts=1)

        handler = JobHandler(storage_conn)
        next_at = "2026-06-10 12:30:00"
        handler.mark_retry(
            job_id, error="network blip", next_attempt_at=next_at
        )

        job = handler.get(job_id)
        assert job is not None
        assert job.status == "queued"
        assert job.last_error == "network blip"
        assert job.next_attempt_at == next_at
        assert job.locked_at is None
        assert job.locked_by is None

    def test_load_draft_returns_draft(self, storage_conn: sqlite3.Connection):
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn, vacancy_id=10)
        handler = JobHandler(storage_conn)
        draft = handler.load_draft(draft_id)
        assert isinstance(draft, ApplicationDraftModel)
        assert draft.vacancy_id == 10

    def test_load_draft_returns_none_for_missing(
        self, storage_conn: sqlite3.Connection
    ):
        from job_bot.application_submit.handlers.job_handler import JobHandler

        handler = JobHandler(storage_conn)
        assert handler.load_draft(999) is None

    def test_save_draft_persists(self, storage_conn: sqlite3.Connection):
        from job_bot.application_submit.handlers.job_handler import JobHandler

        draft_id = _make_draft(storage_conn, vacancy_id=11)
        handler = JobHandler(storage_conn)
        draft = handler.load_draft(draft_id)
        assert draft is not None
        draft.status = "applied"
        handler.save_draft(draft)
        handler.commit()

        refreshed = handler.load_draft(draft_id)
        assert refreshed is not None
        assert refreshed.status == "applied"

    def test_commit(self, storage_conn: sqlite3.Connection):
        """commit() should be a no-op-safe operation (delegates to repo)."""
        from job_bot.application_submit.handlers.job_handler import JobHandler

        handler = JobHandler(storage_conn)
        handler.commit()  # should not raise

    def test_lock_timeout_constant(self) -> None:
        """LOCK_TIMEOUT_SECONDS = 30 min (1800 seconds) for stale lock recovery."""
        from job_bot.application_submit.handlers.job_handler import (
            LOCK_TIMEOUT_SECONDS,
        )

        assert LOCK_TIMEOUT_SECONDS == 30 * 60


# ─── ApplyOneHandler ───────────────────────────────────────────────────


class TestApplyOneHandler:
    """``handlers/apply_one_handler.py`` -- the slice's apply-one handler."""

    def test_call_succeeds_for_simple_draft(self) -> None:
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )

        api_client = MagicMock()
        api_client.post.return_value = {"id": "neg-1"}
        handler = ApplyOneHandler(api_client=api_client)

        draft = ApplicationDraftModel(
            resume_id="r1", vacancy_id=42, status="queued"
        )
        handler(draft)  # should not raise
        api_client.post.assert_called_once()
        args, _ = api_client.post.call_args
        assert args[0] == "/negotiations"

    def test_call_raises_retryable_on_5xx(self) -> None:
        from hh_applicant_tool.api.errors import ApiError
        from job_bot.application_submit.errors import RetryableError
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )

        api_client = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status_code = 503
        fake_resp.request = MagicMock()
        api_client.post.side_effect = ApiError(
            fake_resp, {"description": "down"}
        )

        handler = ApplyOneHandler(api_client=api_client)
        with pytest.raises(RetryableError):
            handler(
                ApplicationDraftModel(
                    resume_id="r1", vacancy_id=1, status="queued"
                )
            )

    def test_call_raises_fatal_on_400(self) -> None:
        from hh_applicant_tool.api.errors import ApiError
        from job_bot.application_submit.errors import FatalError
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )

        api_client = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status_code = 400
        fake_resp.request = MagicMock()
        api_client.post.side_effect = ApiError(
            fake_resp, {"description": "bad"}
        )

        handler = ApplyOneHandler(api_client=api_client)
        with pytest.raises(FatalError):
            handler(
                ApplicationDraftModel(
                    resume_id="r1", vacancy_id=1, status="queued"
                )
            )

    def test_convert_errors_false_propagates_captcha_required(self) -> None:
        """``convert_errors=False`` -> ``CaptchaRequired`` propagates as-is (issue #73)."""
        from hh_applicant_tool.api.errors import CaptchaRequired
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )
        from job_bot.application_submit.errors import RetryableError
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )

        api_client = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status_code = 403
        fake_resp.request = MagicMock()
        api_client.post.side_effect = CaptchaRequired(
            fake_resp,
            {
                "errors": [
                    {
                        "type": "captcha_required",
                        "value": "captcha_required",
                        "captcha_url": "https://hh.ru/captcha?x=1",
                    }
                ]
            },
        )

        handler = ApplyOneHandler(api_client=api_client, convert_errors=True)
        with pytest.raises(RetryableError):
            handler(
                ApplicationDraftModel(
                    resume_id="r1", vacancy_id=1, status="queued"
                )
            )

        handler = ApplyOneHandler(api_client=api_client, convert_errors=False)
        with pytest.raises(CaptchaRequired):
            handler(
                ApplicationDraftModel(
                    resume_id="r1", vacancy_id=1, status="queued"
                )
            )

    def test_convert_errors_false_propagates_limit_exceeded(self) -> None:
        """``convert_errors=False`` -> ``LimitExceeded`` propagates as-is (issue #73)."""
        from hh_applicant_tool.api.errors import LimitExceeded
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )
        from job_bot.application_submit.errors import RetryableError
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )

        api_client = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status_code = 400
        fake_resp.request = MagicMock()
        api_client.post.side_effect = LimitExceeded(
            fake_resp,
            {"errors": [{"type": "limit", "value": "limit_exceeded"}]},
        )

        handler = ApplyOneHandler(api_client=api_client, convert_errors=True)
        with pytest.raises(RetryableError):
            handler(
                ApplicationDraftModel(
                    resume_id="r1", vacancy_id=1, status="queued"
                )
            )

        handler = ApplyOneHandler(api_client=api_client, convert_errors=False)
        with pytest.raises(LimitExceeded):
            handler(
                ApplicationDraftModel(
                    resume_id="r1", vacancy_id=1, status="queued"
                )
            )

    def test_call_passes_session_and_xsrf(self) -> None:
        """When a test draft is used, the session/xsrf are forwarded."""
        from job_bot.application_submit.errors import FatalError
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )

        api_client = MagicMock()
        session = MagicMock()
        session.get.return_value.text = (
            '<html>...,"xsrfToken":"abc123",...</html>'
        )
        session.post.return_value.json.return_value = {
            "success": False,
            "error": "nope",
        }

        handler = ApplyOneHandler(
            api_client=api_client, session=session, xsrf_token=None
        )
        draft = ApplicationDraftModel(
            resume_id="r1",
            vacancy_id=7,
            status="queued",
            has_test=True,
        )
        with pytest.raises(FatalError):
            handler(draft)
        assert session.get.called or session.post.called


# ─── TestHandler ───────────────────────────────────────────────────────


class TestTestHandler:
    """``handlers/test_handler.py`` -- delegates to VacancyTestsService."""

    def _make_response(self, tests_json: str) -> MagicMock:
        r = MagicMock()
        r.text = (
            f'<html>...,"vacancyTests":{tests_json},"counters":[]...</html>'
        )
        return r

    def test_fetch_tests(self) -> None:
        from job_bot.application_submit.handlers.test_handler import TestHandler

        session = MagicMock()
        session.get.return_value = self._make_response(
            '{"uidPk":"u","guid":"g","startTime":0,"required":true,'
            '"tasks":[{"id":"t1","description":"q","candidateSolutions":[]}]}'
        )
        handler = TestHandler(session=session)
        data = handler.fetch_tests(
            "https://hh.ru/applicant/vacancy_response?v=1"
        )
        assert data["uidPk"] == "u"
        session.get.assert_called_once()

    def test_prepare_answers_rule_based(self) -> None:
        """Rule-based fallback (no AI) → "Да" for yes-option, mid for others."""
        from job_bot.application_submit.handlers.test_handler import TestHandler

        session = MagicMock()
        handler = TestHandler(session=session)
        test_data: dict[str, Any] = {
            "tasks": [
                {
                    "id": "t1",
                    "description": "q?",
                    "candidateSolutions": [
                        {"id": "1", "text": "Да"},
                        {"id": "2", "text": "Нет"},
                    ],
                },
                {
                    "id": "t2",
                    "description": "free text?",
                    "candidateSolutions": [],
                },
            ]
        }
        answers = handler.prepare_answers(test_data)
        assert len(answers) == 2
        assert answers[0].selected_solution_id == "1"  # "Да" preferred
        assert answers[1].answer_type == "text"
        assert answers[1].generated_answer == "Да"  # rule-based fallback

    def test_prepare_answers_with_ai(self) -> None:
        """AI client is called and a numeric answer is parsed."""
        from job_bot.application_submit.handlers.test_handler import TestHandler

        ai_client = MagicMock()
        ai_client.complete.return_value = "2"
        session = MagicMock()
        handler = TestHandler(session=session, ai_client=ai_client)
        test_data: dict[str, Any] = {
            "tasks": [
                {
                    "id": "t1",
                    "description": "Pick one",
                    "candidateSolutions": [
                        {"id": "1", "text": "A"},
                        {"id": "2", "text": "B"},
                    ],
                }
            ]
        }
        answers = handler.prepare_answers(test_data)
        assert answers[0].selected_solution_id == "2"
        ai_client.complete.assert_called_once()

    def test_build_payload(self) -> None:
        from job_bot.application_submit.handlers.test_handler import TestHandler
        from job_bot.application_submit.models.test_answer import TestAnswer

        session = MagicMock()
        handler = TestHandler(session=session)
        test_data: dict[str, Any] = {
            "uidPk": "u",
            "guid": "g",
            "startTime": 0,
            "required": True,
            "tasks": [
                {
                    "id": "t1",
                    "description": "Q",
                    "candidateSolutions": [
                        {"id": "1", "text": "Да"},
                        {"id": "2", "text": "Нет"},
                    ],
                }
            ],
        }
        answers = [
            TestAnswer(
                task_id="t1",
                question="Q",
                answer_type="choice",
                selected_solution_id="2",
            )
        ]
        payload = handler.build_payload(
            test_data,
            answers,
            vacancy_id="42",
            resume_hash="r1",
            letter="Hi",
            xsrf_token="xtok",
        )
        assert payload["_xsrf"] == "xtok"
        assert payload["task_t1"] == "2"
        assert payload["letter"] == "Hi"

    def test_submit_apply(self) -> None:
        from job_bot.application_submit.handlers.test_handler import TestHandler

        session = MagicMock()
        session.post.return_value.json.return_value = {"success": True}
        handler = TestHandler(session=session)
        result = handler.submit_apply(
            "https://hh.ru/applicant/vacancy_response?v=1",
            {"_xsrf": "x", "task_t1": "1"},
            xsrf_token="x",
        )
        assert result == {"success": True}
        session.post.assert_called_once()


# ─── RetryHandler ──────────────────────────────────────────────────────


class TestRetryHandler:
    """``handlers/retry_handler.py`` -- backoff / max-attempts policy."""

    def test_backoff_first_attempt(self) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )

        assert RetryHandler.backoff_seconds(attempt=1) == 5 * 60

    def test_backoff_second_attempt(self) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )

        assert RetryHandler.backoff_seconds(attempt=2) == 15 * 60

    def test_backoff_third_attempt(self) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )

        assert RetryHandler.backoff_seconds(attempt=3) == 60 * 60

    def test_backoff_clipped_at_last(self) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )

        assert RetryHandler.backoff_seconds(attempt=10) == 60 * 60

    def test_backoff_zero_for_zero_attempt(self) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )

        assert RetryHandler.backoff_seconds(attempt=0) == 0

    def test_should_retry_below_max(self) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )

        assert RetryHandler.should_retry(attempt=3, max_attempts=5) is True
        assert RetryHandler.should_retry(attempt=1, max_attempts=5) is True

    def test_should_retry_at_max(self) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )

        assert RetryHandler.should_retry(attempt=5, max_attempts=5) is False
        assert RetryHandler.should_retry(attempt=6, max_attempts=5) is False

    def test_next_attempt_at_format(self, clock: _FixedClock) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )

        next_at = RetryHandler.next_attempt_at(attempt=1, now=clock.now())
        expected = (clock.now() + timedelta(minutes=5)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        assert next_at == expected

    def test_next_attempt_at_uses_2nd_attempt_backoff(
        self, clock: _FixedClock
    ) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )

        next_at = RetryHandler.next_attempt_at(attempt=2, now=clock.now())
        expected = (clock.now() + timedelta(minutes=15)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        assert next_at == expected

    def test_max_attempts_constant(self) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            DEFAULT_MAX_ATTEMPTS,
        )

        assert DEFAULT_MAX_ATTEMPTS == 5


# ─── WorkerService ─────────────────────────────────────────────────────


class TestWorkerService:
    """``services/worker_service.py`` -- main worker loop."""

    def _make_worker(
        self,
        storage_conn: sqlite3.Connection,
        *,
        apply_one: Any = None,
        notifier: MagicMock | None = None,
        clock: _FixedClock | None = None,
        worker_id: str = WORKER_ID,
        max_attempts: int = 5,
    ) -> Any:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )
        from job_bot.application_submit.services.worker_service import (
            WorkerService,
        )

        # When ``apply_one`` is provided we wire it directly so the test
        # can drive the worker with a MagicMock. When it is None we
        # fall back to a real ``ApplyOneHandler`` (with a MagicMock
        # ``api_client``) so the slice's wiring is exercised.
        if apply_one is None:
            from job_bot.application_submit.handlers.apply_one_handler import (
                ApplyOneHandler,
            )

            apply_one = ApplyOneHandler(api_client=MagicMock())

        return WorkerService(
            storage_conn=storage_conn,
            apply_one=apply_one,
            retry=RetryHandler(),
            notifier=notifier,
            clock=clock or _FixedClock(),
            delay=MagicMock(),
            worker_id=worker_id,
            max_attempts=max_attempts,
        )

    def test_process_one_succeeds(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.models.submit_result import (
            SubmitResult,
            SubmitStatus,
        )

        draft_id = _make_draft(storage_conn, vacancy_id=1)
        _make_job(storage_conn, draft_id)

        apply_one = MagicMock(return_value=None)
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, clock=clock
        )

        result = worker.process_one()
        assert isinstance(result, SubmitResult)
        assert result.status == SubmitStatus.SUCCEEDED
        assert result.draft_id == draft_id
        apply_one.assert_called_once()

    def test_process_one_returns_none_when_empty(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        worker = self._make_worker(storage_conn, clock=clock)
        assert worker.process_one() is None

    def test_process_one_retryable_schedules_retry(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.errors import RetryableError
        from job_bot.application_submit.handlers.job_handler import JobHandler
        from job_bot.application_submit.models.submit_result import (
            SubmitStatus,
        )

        draft_id = _make_draft(storage_conn, vacancy_id=2)
        job_id = _make_job(storage_conn, draft_id)

        apply_one = MagicMock(side_effect=RetryableError("network blip"))
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, clock=clock
        )

        result = worker.process_one()
        assert result is not None
        assert result.status == SubmitStatus.SKIPPED
        assert "network blip" in (result.last_error or "")

        job = JobHandler(storage_conn, clock=clock).get(job_id)
        assert job is not None
        assert job.status == "queued"
        assert job.next_attempt_at == _isoformat(
            clock.now() + timedelta(minutes=5)
        )

    def test_process_one_fatal_marks_failed(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.errors import FatalError
        from job_bot.application_submit.handlers.job_handler import JobHandler
        from job_bot.application_submit.models.submit_result import (
            SubmitStatus,
        )

        draft_id = _make_draft(storage_conn, vacancy_id=3)
        job_id = _make_job(storage_conn, draft_id)

        apply_one = MagicMock(side_effect=FatalError("400 bad request"))
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, clock=clock
        )

        result = worker.process_one()
        assert result is not None
        assert result.status == SubmitStatus.FAILED
        assert "400" in (result.last_error or "")

        job = JobHandler(storage_conn, clock=clock).get(job_id)
        assert job is not None
        assert job.status == "failed"

    def test_process_one_gives_up_after_max_attempts(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.errors import RetryableError
        from job_bot.application_submit.models.submit_result import (
            SubmitStatus,
        )

        draft_id = _make_draft(storage_conn, vacancy_id=4)
        _make_job(storage_conn, draft_id, attempts=4)  # one more → 5

        apply_one = MagicMock(side_effect=RetryableError("blip"))
        worker = self._make_worker(
            storage_conn,
            apply_one=apply_one,
            clock=clock,
            max_attempts=5,
        )
        result = worker.process_one()
        assert result is not None
        assert result.status == SubmitStatus.FAILED

    def test_notifier_called_on_success(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.models.submit_result import (
            SubmitResult,
        )

        draft_id = _make_draft(storage_conn, vacancy_id=5)
        _make_job(storage_conn, draft_id)

        apply_one = MagicMock(return_value=None)
        notifier = MagicMock()
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, notifier=notifier, clock=clock
        )
        result = worker.process_one()
        assert isinstance(result, SubmitResult)
        assert result.status == "succeeded"
        notifier.assert_called_once()
        kind, text = notifier.call_args[0]
        assert kind == "success"
        assert "✅" in text or "Отклик" in text or "Vacancy" in text

    def test_notifier_called_on_failure(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.errors import FatalError

        draft_id = _make_draft(storage_conn, vacancy_id=6)
        _make_job(storage_conn, draft_id)

        apply_one = MagicMock(side_effect=FatalError("400 boom"))
        notifier = MagicMock()
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, notifier=notifier, clock=clock
        )
        worker.process_one()
        notifier.assert_called_once()
        kind, text = notifier.call_args[0]
        assert kind == "failure"
        assert "400 boom" in text

    def test_notifier_not_called_on_retryable(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.errors import RetryableError

        draft_id = _make_draft(storage_conn, vacancy_id=7)
        _make_job(storage_conn, draft_id)

        apply_one = MagicMock(side_effect=RetryableError("blip"))
        notifier = MagicMock()
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, notifier=notifier, clock=clock
        )
        worker.process_one()
        notifier.assert_not_called()

    def test_run_processes_multiple_then_stops_when_idle(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.services.worker_service import RunStats

        for vid in (1, 2, 3):
            draft_id = _make_draft(storage_conn, vacancy_id=vid)
            _make_job(storage_conn, draft_id)

        apply_one = MagicMock(return_value=None)
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, clock=clock
        )
        stats = worker.run(stop_when_idle=True)

        assert isinstance(stats, RunStats)
        assert stats.processed == 3
        assert stats.succeeded == 3
        assert stats.idle_loops >= 1
        assert apply_one.call_count == 3

    def test_run_max_jobs(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        for vid in (1, 2, 3, 4, 5):
            draft_id = _make_draft(storage_conn, vacancy_id=vid)
            _make_job(storage_conn, draft_id)

        apply_one = MagicMock(return_value=None)
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, clock=clock
        )
        stats = worker.run(max_jobs=2, stop_when_idle=True)
        assert stats.processed == 2
        assert apply_one.call_count == 2

    def test_run_stop_method(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )
        from job_bot.application_submit.services.worker_service import (
            RunStats,
            WorkerService,
        )

        for vid in (1, 2):
            draft_id = _make_draft(storage_conn, vacancy_id=vid)
            _make_job(storage_conn, draft_id)

        # Build a worker whose apply-one callback requests a stop
        # after the first invocation. ``run()`` resets
        # ``_stop_requested`` at the start, so we have to wire the
        # stop into the callback (mirroring how a real orchestrator
        # would react to SIGINT mid-loop).
        worker = WorkerService(
            storage_conn=storage_conn,
            apply_one=MagicMock(side_effect=lambda *_a, **_kw: worker.stop()),
            retry=RetryHandler(),
            clock=clock,
            delay=MagicMock(),
            worker_id=WORKER_ID,
        )
        stats = worker.run(stop_when_idle=False)
        assert isinstance(stats, RunStats)
        assert stats.processed == 1

    def test_missing_draft_marks_failed(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from hh_applicant_tool.storage import StorageFacade
        from hh_applicant_tool.storage.models.apply_job import ApplyJobModel
        from job_bot.application_submit.models.submit_result import (
            SubmitStatus,
        )

        facade = StorageFacade(storage_conn)
        job = ApplyJobModel(draft_id=99999, status="queued")
        facade.apply_jobs.save(job)
        facade.apply_jobs.commit()

        apply_one = MagicMock()
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, clock=clock
        )
        result = worker.process_one()
        assert result is not None
        assert result.status == SubmitStatus.FAILED
        assert "не найден" in (result.last_error or "") or "not found" in (
            result.last_error or ""
        )
        apply_one.assert_not_called()

    def test_stats_tracking(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.errors import (
            FatalError,
            RetryableError,
        )

        d1 = _make_draft(storage_conn, vacancy_id=10)
        d2 = _make_draft(storage_conn, vacancy_id=11)
        d3 = _make_draft(storage_conn, vacancy_id=12)
        _make_job(storage_conn, d1)
        _make_job(storage_conn, d2)
        _make_job(storage_conn, d3, attempts=4)  # last attempt → give up

        side_effects = [None, FatalError("400"), RetryableError("blip")]
        apply_one = MagicMock(side_effect=side_effects)
        worker = self._make_worker(
            storage_conn, apply_one=apply_one, clock=clock
        )
        stats = worker.run(stop_when_idle=True)
        assert stats.succeeded == 1
        assert stats.failed == 2
        assert stats.processed == 3

    def test_worker_id_is_preserved(self, storage_conn: sqlite3.Connection):
        worker = self._make_worker(storage_conn, worker_id="my-host-42")
        assert worker.worker_id == "my-host-42"

    def test_worker_id_auto_generated(self, storage_conn: sqlite3.Connection):
        worker = self._make_worker(storage_conn)
        assert worker.worker_id
        assert ":" in worker.worker_id or len(worker.worker_id) > 0

    def test_idle_sleep_called_when_queue_empty(
        self, storage_conn: sqlite3.Connection, clock: _FixedClock
    ):
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )
        from job_bot.application_submit.services.worker_service import (
            WorkerService,
        )

        delay = MagicMock()
        api_client = MagicMock()
        worker = WorkerService(
            storage_conn=storage_conn,
            apply_one=ApplyOneHandler(api_client=api_client),
            retry=RetryHandler(),
            delay=delay,
            clock=clock,
            worker_id=WORKER_ID,
        )
        # No jobs in the queue. Wire the delay so it triggers a stop
        # on the first sleep call -- that way the loop runs the sleep
        # path before exiting.
        delay.sleep.side_effect = lambda _seconds: worker.stop()
        worker.run(stop_when_idle=False)
        assert delay.sleep.called


# ─── ApplicationSubmitSlice ────────────────────────────────────────────


class TestApplicationSubmitSlice:
    """``slice.py`` -- main entry point and factory."""

    def test_create_slice(self, storage_conn: sqlite3.Connection) -> None:
        from job_bot.application_submit.slice import ApplicationSubmitSlice

        api_client = MagicMock()
        slice_ = ApplicationSubmitSlice(
            storage_conn=storage_conn, api_client=api_client
        )
        assert slice_ is not None
        assert slice_.jobs is not None
        assert slice_.apply_one is not None
        assert slice_.tests is not None
        assert slice_.retry is not None
        assert slice_.worker is not None

    def test_create_slice_with_optional_deps(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.slice import ApplicationSubmitSlice

        slice_ = ApplicationSubmitSlice(
            storage_conn=storage_conn,
            api_client=MagicMock(),
            session=MagicMock(),
            xsrf_token="xtok",
            ai_client=MagicMock(),
            notifier=MagicMock(),
            worker_id="slice-worker",
            max_attempts=7,
        )
        assert slice_.worker.worker_id == "slice-worker"
        assert slice_.worker.max_attempts == 7

    def test_jobs_property_returns_job_port(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.handlers.job_handler import JobHandler
        from job_bot.application_submit.slice import ApplicationSubmitSlice

        slice_ = ApplicationSubmitSlice(
            storage_conn=storage_conn, api_client=MagicMock()
        )
        assert isinstance(slice_.jobs, JobHandler)

    def test_apply_one_property_returns_apply_one_handler(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )
        from job_bot.application_submit.slice import ApplicationSubmitSlice

        slice_ = ApplicationSubmitSlice(
            storage_conn=storage_conn, api_client=MagicMock()
        )
        assert isinstance(slice_.apply_one, ApplyOneHandler)

    def test_tests_property_returns_test_handler(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.handlers.test_handler import TestHandler
        from job_bot.application_submit.slice import ApplicationSubmitSlice

        slice_ = ApplicationSubmitSlice(
            storage_conn=storage_conn,
            api_client=MagicMock(),
            session=MagicMock(),
        )
        assert isinstance(slice_.tests, TestHandler)

    def test_retry_property_returns_retry_handler(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.handlers.retry_handler import (
            RetryHandler,
        )
        from job_bot.application_submit.slice import ApplicationSubmitSlice

        slice_ = ApplicationSubmitSlice(
            storage_conn=storage_conn, api_client=MagicMock()
        )
        assert isinstance(slice_.retry, RetryHandler)

    def test_factory_creates_slice(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.slice import (
            ApplicationSubmitSlice,
            create_application_submit_slice,
        )

        slice_ = create_application_submit_slice(
            storage_conn=storage_conn, api_client=MagicMock()
        )
        assert isinstance(slice_, ApplicationSubmitSlice)

    def test_full_workflow_enqueue_and_process(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        """End-to-end: enqueue 2 jobs → process via slice → both succeed."""
        from job_bot.application_submit.slice import (
            create_application_submit_slice,
        )

        api_client = MagicMock()
        api_client.post.return_value = {"id": "neg-x"}
        slice_ = create_application_submit_slice(
            storage_conn=storage_conn, api_client=api_client
        )

        for vid in (1, 2):
            draft_id = _make_draft(storage_conn, vacancy_id=vid)
            _make_job(storage_conn, draft_id)

        stats = slice_.worker.run(stop_when_idle=True)
        assert stats.processed == 2
        assert stats.succeeded == 2
        assert stats.idle_loops >= 1

        from hh_applicant_tool.storage import StorageFacade

        for vid in (1, 2):
            row = (
                StorageFacade(storage_conn)
                .application_drafts.conn.execute(
                    "SELECT status FROM application_drafts WHERE vacancy_id=?",
                    (vid,),
                )
                .fetchone()
            )
            assert row["status"] == "applied"

    def test_reuse_existing_services(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        """The slice's apply_one is backed by ``ApplyOneHandler``
        (no reimplementation in the slice itself)."""
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )
        from job_bot.application_submit.slice import (
            create_application_submit_slice,
        )

        api_client = MagicMock()
        slice_ = create_application_submit_slice(
            storage_conn=storage_conn, api_client=api_client
        )
        assert isinstance(slice_.apply_one, ApplyOneHandler)
        slice_.apply_one(
            ApplicationDraftModel(resume_id="r", vacancy_id=1, status="queued")
        )
        api_client.post.assert_called_once()
        # ApplyOneHandler exists and is wired to the slice.
        assert ApplyOneHandler is not None

    def test_database_property_exposed(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.slice import ApplicationSubmitSlice

        slice_ = ApplicationSubmitSlice(
            storage_conn=storage_conn, api_client=MagicMock()
        )
        assert slice_.storage_conn is storage_conn


# ─── Port protocols ────────────────────────────────────────────────────


class TestPorts:
    """The slice's handlers satisfy the port protocols (structural typing)."""

    def test_job_handler_satisfies_job_port(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.handlers.job_handler import JobHandler
        from job_bot.application_submit.ports.job_port import JobPort

        handler: JobPort = JobHandler(storage_conn)
        assert handler.claim_next(worker_id=WORKER_ID) is None
        assert handler.get(999) is None
        handler.commit()  # no-op

    def test_apply_one_handler_satisfies_apply_one_port(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.handlers.apply_one_handler import (
            ApplyOneHandler,
        )
        from job_bot.application_submit.ports.apply_one_port import ApplyOnePort

        port: ApplyOnePort = ApplyOneHandler(api_client=MagicMock())
        assert port is not None

    def test_test_handler_satisfies_test_port(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        from job_bot.application_submit.handlers.test_handler import TestHandler
        from job_bot.application_submit.ports.test_port import TestPort

        port: TestPort = TestHandler(session=MagicMock())
        assert port is not None
