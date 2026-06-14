"""E2E: prepare-vacancies -> apply-worker -> draft applied (issues #54, #55).

End-to-end pipeline that powers the ``prepare-vacancies`` and
``apply`` CLI commands: the application prep slice writes
``ApplicationDraftModel`` rows, the application submit slice claims
them through ``apply_jobs``, and the worker POSTs to
``/negotiations`` against the mocked HH API.
"""

from __future__ import annotations


import pytest

from tests.integration._mocks import NoOpDelay

pytestmark = pytest.mark.integration


# ─── Helpers ──────────────────────────────────────────────────────────


def _make_full_vacancy(vacancy_id: int | str) -> dict:
    """Build a realistic ``/vacancies/{id}``-shaped payload."""
    return {
        "id": str(vacancy_id),
        "name": f"Vacancy {vacancy_id}",
        "employer": {"id": 900 + int(vacancy_id), "name": "Acme"},
        "salary": {"from": 200000, "to": 300000, "currency": "RUR"},
        "area": {"name": "Москва"},
        "description": "Backend role with Python, Django, FastAPI.",
        "key_skills": [
            {"name": "Python"},
            {"name": "Django"},
            {"name": "PostgreSQL"},
        ],
        "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        "has_test": False,
        "response_letter_required": True,
    }


def _make_draft(
    conn,
    *,
    vacancy_id: int,
    resume_id: str = "r1",
    status: str = "prepared",
) -> int:
    """Insert an ``application_drafts`` row and return its id."""
    from hh_applicant_tool.storage import StorageFacade
    from hh_applicant_tool.storage.models.application_draft import (
        ApplicationDraftModel,
    )

    facade = StorageFacade(conn)
    draft = ApplicationDraftModel(
        resume_id=resume_id,
        vacancy_id=vacancy_id,
        status=status,
        cover_letter="Hi, I'm a strong fit.",
        cover_letter_status="generated",
        full_vacancy_json=_make_full_vacancy(vacancy_id),
        hh_response_url=f"https://hh.ru/vacancy/{vacancy_id}",
    )
    facade.application_drafts.save(draft)
    facade.application_drafts.commit()
    row = facade.application_drafts.conn.execute(
        "SELECT id FROM application_drafts WHERE vacancy_id=?",
        (vacancy_id,),
    ).fetchone()
    return row["id"]


def _make_job(
    conn,
    draft_id: int,
    *,
    status: str = "queued",
) -> int:
    from hh_applicant_tool.storage import StorageFacade
    from hh_applicant_tool.storage.models.apply_job import ApplyJobModel

    facade = StorageFacade(conn)
    facade.apply_jobs.save(ApplyJobModel(draft_id=draft_id, status=status))
    facade.apply_jobs.commit()
    row = facade.apply_jobs.conn.execute(
        "SELECT id FROM apply_jobs WHERE draft_id=?", (draft_id,)
    ).fetchone()
    return row["id"]


def _build_worker(test_db, apply_one, *, worker_id: str):
    """Build a :class:`WorkerService` with no-op delay (no real sleep)."""
    from job_bot.application_submit.handlers.retry_handler import (
        RetryHandler,
    )
    from job_bot.application_submit.services.worker_service import (
        WorkerService,
    )

    return WorkerService(
        storage_conn=test_db,
        apply_one=apply_one,
        retry=RetryHandler(),
        delay=NoOpDelay(),
        worker_id=worker_id,
    )


# ─── Test cases ──────────────────────────────────────────────────────


class TestPrepareToSubmitFlow:
    """End-to-end prepare-vacancies -> apply-worker flow."""

    @pytest.mark.xfail(
        reason="pre-existing, see #100. Two stacked issues: (1) #104: sqlite3.IntegrityError 'datatype mismatch' on application_drafts INSERT — legacy schema.sql declares id INTEGER PRIMARY KEY AUTOINCREMENT but the VSA ApplicationDraftRepository writes TEXT UUIDs. (2) #102: MockHHApiResponse is requests.Response-shaped; the prepare_draft path calls .get() on the response before .json() is taken. The test fails on (2) first, masking (1)."
    )
    def test_prepare_then_submit_succeeds(
        self,
        test_db,
        mock_hh_api,
        slices,
    ) -> None:
        """Two drafts are prepared, claimed by the worker, and applied
        via the mocked ``/negotiations`` endpoint.

        Acceptance: every draft row moves through
        ``prepared -> applied``, the apply_jobs row ends in
        ``succeeded``, and exactly two ``/negotiations`` calls were
        made.
        """
        vacancy_ids = [101, 102]

        # 1) Prepare: use the application prep slice directly. This
        #    mirrors what ``PrepareVacanciesUseCase`` does in
        #    production for each (profile, vacancy) pair (we test
        #    the full use-case wiring separately in
        #    test_vsa_*_wiring).
        for vid in vacancy_ids:
            full_vacancy = _make_full_vacancy(vid)
            draft = slices.application_prep.applications.prepare_draft(
                resume={"id": "r1", "title": "Senior Python"},
                vacancy=full_vacancy,
                search_profile_id="p1",
                ai_filter_mode="none",
                placeholders={"first_name": "Ivan"},
                force_message=True,
            )
            assert draft is not None
            assert draft.status in {"prepared", "rejected"}

        # 2) Enqueue: insert apply_jobs rows referencing the drafts.
        for vid in vacancy_ids:
            draft_id = _make_draft(test_db, vacancy_id=vid)
            _make_job(test_db, draft_id)

        # 3) Submit: the worker should claim the rows and POST to
        #    /negotiations.
        worker = _build_worker(
            test_db,
            apply_one=slices.application_submit.apply_one,
            worker_id="test-integration-worker",
        )
        stats = worker.run(stop_when_idle=True)

        # 4) Verify state machine: drafts are applied, jobs are
        #    succeeded.
        assert stats.processed == 2
        assert stats.succeeded == 2

        from hh_applicant_tool.storage import StorageFacade

        facade = StorageFacade(test_db)
        for vid in vacancy_ids:
            row = facade.application_drafts.conn.execute(
                "SELECT status, hh_response_url FROM application_drafts "
                "WHERE vacancy_id = ?",
                (vid,),
            ).fetchone()
            assert row is not None
            assert row["status"] == "applied", (
                f"vacancy {vid} draft should be 'applied', got {row['status']}"
            )
            assert row["hh_response_url"] == f"https://hh.ru/vacancy/{vid}"

            job_row = facade.apply_jobs.conn.execute(
                "SELECT status FROM apply_jobs j "
                "JOIN application_drafts d ON j.draft_id = d.id "
                "WHERE d.vacancy_id = ?",
                (vid,),
            ).fetchone()
            assert job_row["status"] == "succeeded"

        # /negotiations was hit exactly twice (no extras, no misses)
        neg_calls = [c for c in mock_hh_api.calls if c[1] == "/negotiations"]
        assert len(neg_calls) == 2

    def test_apply_worker_drains_apply_jobs_table(
        self,
        test_db,
        slices,
    ) -> None:
        """No row remains in ``running`` state after the worker drains
        the queue.

        The ``running`` status corresponds to the worker having
        claimed a row but not yet committed. After
        ``run(stop_when_idle=True)``, every claimed row should have
        been moved to a terminal state.
        """
        for vid in (200, 201, 202):
            draft_id = _make_draft(test_db, vacancy_id=vid)
            _make_job(test_db, draft_id)

        worker = _build_worker(
            test_db,
            apply_one=slices.application_submit.apply_one,
            worker_id="drain-worker",
        )
        worker.run(stop_when_idle=True)

        from hh_applicant_tool.storage import StorageFacade

        facade = StorageFacade(test_db)
        running = facade.apply_jobs.conn.execute(
            "SELECT COUNT(*) AS n FROM apply_jobs WHERE status = 'running'"
        ).fetchone()
        assert running["n"] == 0, (
            "no apply_jobs row should remain in 'running' after drain"
        )

        succeeded = facade.apply_jobs.conn.execute(
            "SELECT COUNT(*) AS n FROM apply_jobs WHERE status = 'succeeded'"
        ).fetchone()
        assert succeeded["n"] == 3

    def test_apply_worker_handles_retryable_failure(
        self,
        test_db,
    ) -> None:
        """A :class:`RetryableError` from ``apply_one`` moves the job
        to ``queued`` with a ``next_attempt_at`` set (status is not
        terminal — the slice's retry handler kicks in).
        """
        from job_bot.application_submit.services.apply_worker_service import (
            RetryableError,
        )
        from hh_applicant_tool.storage import StorageFacade

        draft_id = _make_draft(test_db, vacancy_id=999)
        job_id = _make_job(test_db, draft_id)

        def _explode(_draft):
            raise RetryableError("network blip")

        worker = _build_worker(
            test_db,
            apply_one=_explode,  # type: ignore[arg-type]
            worker_id="retry-worker",
        )
        stats = worker.run(stop_when_idle=True)
        assert stats.processed == 1
        # Job is back in queued (retry) state, not failed
        row = (
            StorageFacade(test_db)
            .apply_jobs.conn.execute(
                "SELECT status, next_attempt_at FROM apply_jobs WHERE id=?",
                (job_id,),
            )
            .fetchone()
        )
        assert row["status"] == "queued"
        assert row["next_attempt_at"] is not None
