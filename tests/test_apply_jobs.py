"""Тесты репозитория apply_jobs (issue #1)."""

from __future__ import annotations

import sqlite3

from job_bot._legacy_compat.storage.facade import StorageFacade
from job_bot._legacy_compat.storage.models.apply_job import ApplyJobModel


def _make_draft(facade: StorageFacade, vacancy_id: int = 1) -> int:
    from job_bot._legacy_compat.storage.models.application_draft import (
        ApplicationDraftModel,
    )

    facade.application_drafts.save(
        ApplicationDraftModel(resume_id="r1", vacancy_id=vacancy_id)
    )
    storage = facade.application_drafts.conn
    storage.commit()
    row = storage.execute(
        "SELECT id FROM application_drafts WHERE vacancy_id=?",
        (vacancy_id,),
    ).fetchone()
    return row["id"]


def test_insert_and_find_queued(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade)

    facade.apply_jobs.save(ApplyJobModel(draft_id=draft_id, status="queued"))
    storage.commit()

    assert facade.apply_jobs.count_total() == 1
    queued = list(facade.apply_jobs.find(status="queued"))
    assert len(queued) == 1
    assert queued[0].draft_id == draft_id
    assert queued[0].attempts == 0
    assert queued[0].max_attempts == 3


def test_unique_per_draft(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade)

    facade.apply_jobs.save(ApplyJobModel(draft_id=draft_id, status="queued"))
    facade.apply_jobs.save(ApplyJobModel(draft_id=draft_id, status="running"))
    storage.commit()

    # UPSERT: один job на черновик, статус обновлён
    assert facade.apply_jobs.count_total() == 1
    jobs = list(facade.apply_jobs.find(draft_id=draft_id))
    assert jobs[0].status == "running"


def test_retry_state_transitions(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade)

    facade.apply_jobs.save(
        ApplyJobModel(
            draft_id=draft_id,
            status="queued",
            attempts=0,
            max_attempts=3,
        )
    )
    storage.commit()

    # Забираем в работу
    job = list(facade.apply_jobs.find(draft_id=draft_id))[0]
    job.status = "running"
    job.attempts = 1
    job.locked_at = "2026-06-07 12:00:00"
    job.locked_by = "worker-1"
    facade.apply_jobs.save(job)
    storage.commit()

    # RetryableError → возврат в очередь
    job.status = "queued"
    job.attempts = 1
    job.locked_at = None
    job.locked_by = None
    job.next_attempt_at = "2026-06-07 12:05:00"
    job.last_error = "captcha required"
    facade.apply_jobs.save(job)
    storage.commit()

    fetched = list(facade.apply_jobs.find(draft_id=draft_id))[0]
    assert fetched.status == "queued"
    assert fetched.attempts == 1
    assert fetched.last_error == "captcha required"
    assert fetched.locked_by is None


def test_terminal_failure_state(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade)

    facade.apply_jobs.save(
        ApplyJobModel(
            draft_id=draft_id,
            status="failed",
            attempts=3,
            max_attempts=3,
            last_error="max retries exceeded",
        )
    )
    storage.commit()

    fetched = list(facade.apply_jobs.find(draft_id=draft_id))[0]
    assert fetched.status == "failed"
    assert fetched.attempts == 3
    assert fetched.max_attempts == 3


def test_find_due_for_processing(storage: sqlite3.Connection):
    """Воркер должен выбирать queued с next_attempt_at <= now."""
    facade = StorageFacade(storage)
    d_due = _make_draft(facade, vacancy_id=1)
    d_future = _make_draft(facade, vacancy_id=2)

    facade.apply_jobs.save(
        ApplyJobModel(
            draft_id=d_due,
            status="queued",
            next_attempt_at="2020-01-01 00:00:00",  # давно пора
        )
    )
    facade.apply_jobs.save(
        ApplyJobModel(
            draft_id=d_future,
            status="queued",
            next_attempt_at="2099-01-01 00:00:00",  # в будущем
        )
    )
    storage.commit()

    due = list(
        facade.apply_jobs.find(
            status="queued", next_attempt_at__le="2026-06-07 12:00:00"
        )
    )
    assert len(due) == 1
    assert due[0].draft_id == d_due
