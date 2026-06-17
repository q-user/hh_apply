"""Tests for :class:`DraftPersisterService` (issue #147).

Covers the per-phase service that owns the write-side persistence
helpers: ``save_vacancy``, ``save_employer``, ``save_skipped_ai_rejected``,
and the ``save_vsa_draft_to_legacy_storage`` VSA→legacy shim.

Strategy
--------

* **Storage** — a real :class:`hh_applicant_tool.storage.facade.StorageFacade`
  backed by an in-memory ``:memory:`` SQLite connection (initialised
  via ``init_db``). This proves the upsert paths work end-to-end
  against the canonical legacy schema (issue #147 acceptance
  criterion: "real ``DraftPersister`` repository, real ``:memory:``
  SQLite DB").
* **API client** — a tiny fake recording ``/employers/{id}`` calls
  and returning a canned employer dict.
* **VSA draft** — uses the real
  :class:`job_bot.application_prep.models.application.ApplicationDraft`
  dataclass (built via ``ApplicationDraftRepository.create``) so the
  ``save_vsa_draft_to_legacy_storage`` shim is exercised against the
  same value-object the production path uses.

The tests cover:

* ``save_vacancy`` writes the ``vacancies`` row + a ``vacancy_contacts``
  row when ``vacancy["contacts"]`` is present;
* ``save_vacancy`` logs and continues on a vacancy repo error;
* ``save_vacancy`` is a no-op on the contacts side when no
  ``contacts`` key is present;
* ``save_employer`` fetches ``/employers/{id}`` and writes a row;
* ``save_employer`` silently returns when the vacancy has no employer;
* ``save_employer`` silently returns on API error;
* ``save_skipped_ai_rejected`` writes a row with ``reason='ai_rejected'``;
* ``save_skipped_ai_rejected`` uses ``clock.now()`` when a clock is
  provided;
* ``save_vsa_draft_to_legacy_storage`` upserts a VSA draft to the
  legacy facade and re-reads it;
* ``save_vsa_draft_to_legacy_storage`` returns ``None`` for a ``None``
  VSA draft (the filter rejected the vacancy before save).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from job_bot._legacy_compat.storage.facade import StorageFacade
from job_bot._legacy_compat.storage.utils import init_db
from job_bot.application_prep.models.application import ApplicationDraft
from job_bot.application_prep.services.draft_persister import (
    DraftPersisterService,
)


class _FakeApi:
    """Tiny in-process HH API fake for employer fetches."""

    def __init__(
        self,
        *,
        employer: dict[str, Any] | None = None,
        raise_on: set[str] | None = None,
    ) -> None:
        self._employer = employer
        self._raise_on = raise_on or set()
        self.calls: list[str] = []

    def get(self, endpoint: str, params: Any = None) -> dict[str, Any]:
        import requests

        self.calls.append(endpoint)
        if endpoint in self._raise_on:
            raise requests.RequestException("api boom")
        if endpoint.startswith("/employers/"):
            return self._employer or {
                "id": int(endpoint.rsplit("/", 1)[-1]),
                "name": "Acme",
                "type": "company",
                "site_url": "",
            }
        return {}


def _make_facade() -> tuple[sqlite3.Connection, StorageFacade]:
    """Return ``(conn, facade)`` for an in-memory legacy schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn, StorageFacade(conn)


def _make_draft(
    *,
    draft_id: str = "draft-1",
    search_profile_id: str | None = "p1",
    resume_id: str | None = "r1",
    vacancy_id: int = 1,
    status: str = "prepared",
    relevance_score: int = 80,
    relevance_reason: str = "ok",
    cover_letter: str | None = "Hello.",
    cover_letter_status: str | None = "generated",
    has_test: bool = False,
    test_status: str | None = None,
) -> ApplicationDraft:
    return ApplicationDraft(
        id=draft_id,
        search_profile_id=search_profile_id,
        resume_id=resume_id,
        vacancy_id=vacancy_id,
        employer_id=42,
        status=status,
        relevance_score=relevance_score,
        relevance_reason=relevance_reason,
        analysis_json={"suitable": True, "score": relevance_score},
        full_vacancy_json={"id": vacancy_id, "name": "X"},
        cover_letter=cover_letter,
        cover_letter_status=cover_letter_status,
        has_test=has_test,
        test_status=test_status,
        created_at=datetime(2026, 1, 1).isoformat(),
        updated_at=datetime(2026, 1, 1).isoformat(),
    )


# ─── save_vacancy ──────────────────────────────────────────────────


class TestSaveVacancy:
    """``save_vacancy`` persists a vacancy (+ optional contacts)."""

    def test_writes_vacancy_row(self) -> None:
        conn, facade = _make_facade()
        try:
            service = DraftPersisterService(storage=facade)

            service.save_vacancy(
                {
                    "id": 1,
                    "name": "Senior Python",
                    "alternate_url": "https://hh.ru/vacancy/1",
                    "area": {"id": 1, "name": "Moscow"},
                }
            )
            conn.commit()

            rows = list(facade.vacancies.find())
            assert len(rows) == 1
            assert rows[0].id == 1
            assert rows[0].name == "Senior Python"
        finally:
            conn.close()

    def test_writes_contacts_when_present(self) -> None:
        conn, facade = _make_facade()
        try:
            service = DraftPersisterService(storage=facade)

            service.save_vacancy(
                {
                    "id": 1,
                    "name": "X",
                    "alternate_url": "https://hh.ru/vacancy/1",
                    "area": {"id": 1, "name": "Moscow"},
                    "contacts": {
                        "name": "Recruiter",
                        "email": "r@example.com",
                        "phones": [],
                    },
                }
            )
            conn.commit()

            # ``vacancy_contacts`` shares the same row layout as
            # ``vacancies``; the repo keys on ``id`` so a row exists
            # in the contacts table.
            rows = list(facade.vacancy_contacts.find())
            assert len(rows) == 1
        finally:
            conn.close()

    def test_no_contacts_skips_contacts_save(self) -> None:
        conn, facade = _make_facade()
        try:
            service = DraftPersisterService(storage=facade)

            service.save_vacancy(
                {
                    "id": 1,
                    "name": "X",
                    "alternate_url": "https://hh.ru/vacancy/1",
                    "area": {"id": 1, "name": "Moscow"},
                }
            )
            conn.commit()

            assert list(facade.vacancy_contacts.find()) == []
        finally:
            conn.close()

    def test_vacancy_save_failure_does_not_propagate(self) -> None:
        """A ``RepositoryError`` from the vacancy repo is logged at
        DEBUG and the service continues (the legacy contract)."""
        conn, facade = _make_facade()
        try:

            class _ExplodingRepo:
                def save(self, _v: Any) -> None:
                    from job_bot._legacy_compat.storage.repositories.errors import (
                        RepositoryError,
                    )

                    raise RepositoryError("boom")

            facade.vacancies = _ExplodingRepo()  # type: ignore[assignment]
            service = DraftPersisterService(storage=facade)

            # Should not raise.
            service.save_vacancy({"id": 1, "name": "X"})
        finally:
            conn.close()


# ─── save_employer ─────────────────────────────────────────────────


class TestSaveEmployer:
    """``save_employer`` fetches ``/employers/{id}`` and persists."""

    def test_writes_employer_row(self) -> None:
        conn, facade = _make_facade()
        try:
            api = _FakeApi(
                employer={"id": 42, "name": "Acme", "type": "company"}
            )
            service = DraftPersisterService(storage=facade)

            service.save_employer(
                {"id": 1, "employer": {"id": 42, "name": "Acme"}},
                api_client=api,
            )
            conn.commit()

            assert any(facade.employers.find(id=42))
            assert api.calls == ["/employers/42"]
        finally:
            conn.close()

    def test_no_employer_skips_fetch(self) -> None:
        conn, facade = _make_facade()
        try:
            api = _FakeApi()
            service = DraftPersisterService(storage=facade)

            service.save_employer({"id": 1}, api_client=api)
            service.save_employer({"id": 1, "employer": None}, api_client=api)
            service.save_employer({"id": 1, "employer": {}}, api_client=api)

            assert api.calls == []
        finally:
            conn.close()

    def test_api_error_is_silently_returned(self) -> None:
        conn, facade = _make_facade()
        try:
            api = _FakeApi(raise_on={"/employers/42"})
            service = DraftPersisterService(storage=facade)

            # Should not raise.
            service.save_employer(
                {"id": 1, "employer": {"id": 42}}, api_client=api
            )

            assert not any(facade.employers.find(id=42))
        finally:
            conn.close()


# ─── save_skipped_ai_rejected ──────────────────────────────────────


class TestSaveSkippedAiRejected:
    """``save_skipped_ai_rejected`` writes the skip row."""

    def test_writes_row_with_ai_rejected_reason(self) -> None:
        conn, facade = _make_facade()
        try:
            service = DraftPersisterService(storage=facade)

            service.save_skipped_ai_rejected(
                {
                    "id": 1,
                    "name": "Senior",
                    "alternate_url": "https://hh.ru/vacancy/1",
                    "employer": {"name": "Acme"},
                },
                resume_id="r1",
            )
            conn.commit()

            rows = list(facade.skipped_vacancies.find(reason="ai_rejected"))
            assert len(rows) == 1
            assert rows[0].vacancy_id == 1
            assert rows[0].resume_id == "r1"
            assert rows[0].name == "Senior"
            assert rows[0].employer_name == "Acme"
        finally:
            conn.close()

    def test_clock_now_is_used_when_clock_provided(self) -> None:
        """When a ``clock`` is wired in, ``clock.now()`` is consulted
        for the ``created_at`` value the service tries to write. The
        legacy ``BaseRepository._insert`` strips ``created_at`` from
        the INSERT and lets the SQLite ``DEFAULT CURRENT_TIMESTAMP``
        column fill in the real value, so the test asserts the
        service consulted the clock (not the final stored value)."""
        conn, facade = _make_facade()
        try:
            clock_time = datetime(2030, 6, 15, 12, 0, 0)

            class _RecordingClock:
                def __init__(self, value: datetime) -> None:
                    self._value = value
                    self.calls = 0

                def now(self) -> datetime:
                    self.calls += 1
                    return self._value

            clock = _RecordingClock(clock_time)
            service = DraftPersisterService(storage=facade, clock=clock)

            service.save_skipped_ai_rejected(
                {"id": 1, "name": "X"}, resume_id="r1"
            )
            conn.commit()

            assert clock.calls == 1
            rows = list(facade.skipped_vacancies.find())
            assert len(rows) == 1
        finally:
            conn.close()

    def test_missing_employer_does_not_crash(self) -> None:
        conn, facade = _make_facade()
        try:
            service = DraftPersisterService(storage=facade)

            service.save_skipped_ai_rejected(
                {"id": 1, "name": "X"}, resume_id="r1"
            )
            conn.commit()

            rows = list(facade.skipped_vacancies.find())
            assert len(rows) == 1
            assert rows[0].employer_name is None
        finally:
            conn.close()


# ─── save_vsa_draft_to_legacy_storage ──────────────────────────────


class TestSaveVsaDraftToLegacyStorage:
    """VSA :class:`ApplicationDraft` → legacy
    :class:`hh_applicant_tool.storage.facade.StorageFacade` shim.

    TODO(#158): remove when hh_applicant_tool is gone.
    """

    def test_upserts_vsa_draft_into_legacy_storage(self) -> None:
        conn, facade = _make_facade()
        try:
            service = DraftPersisterService(storage=facade)
            draft = _make_draft(
                draft_id="d-uuid-1",
                resume_id="r1",
                vacancy_id=1,
                status="prepared",
                cover_letter="Hello.",
            )

            saved = service.save_vsa_draft_to_legacy_storage(
                draft, {"id": "r1"}
            )
            conn.commit()

            assert saved is not None
            # Re-read by (resume_id, vacancy_id) to obtain the
            # autoincrement int id assigned by the legacy facade.
            assert saved.id is not None
            assert saved.resume_id == "r1"
            assert saved.vacancy_id == 1
            assert saved.status == "prepared"
            assert saved.cover_letter == "Hello."
            assert saved.cover_letter_status == "generated"
            assert saved.search_profile_id == "p1"

            # The legacy facade really holds the row.
            fetched = facade.application_drafts.get_by_resume_vacancy("r1", 1)
            assert fetched is not None
            assert fetched.status == "prepared"
        finally:
            conn.close()

    def test_returns_none_for_none_vsa_draft(self) -> None:
        """``vsa_draft=None`` means the filter rejected the vacancy
        before save; the shim returns ``None`` so the caller can
        increment the skipped counter."""
        conn, facade = _make_facade()
        try:
            service = DraftPersisterService(storage=facade)
            result = service.save_vsa_draft_to_legacy_storage(
                None, {"id": "r1"}
            )
            assert result is None
        finally:
            conn.close()

    def test_rejected_status_is_preserved(self) -> None:
        conn, facade = _make_facade()
        try:
            service = DraftPersisterService(storage=facade)
            draft = _make_draft(
                status="rejected",
                relevance_score=10,
                relevance_reason="wrong stack",
            )

            saved = service.save_vsa_draft_to_legacy_storage(
                draft, {"id": "r1"}
            )
            conn.commit()

            assert saved is not None
            assert saved.status == "rejected"
            assert saved.relevance_score == 10
            assert saved.relevance_reason == "wrong stack"
        finally:
            conn.close()

    def test_upsert_replaces_existing_row(self) -> None:
        """Re-saving the same (resume_id, vacancy_id) pair updates the
        existing row (UPSERT) instead of failing or duplicating."""
        conn, facade = _make_facade()
        try:
            service = DraftPersisterService(storage=facade)

            draft_v1 = _make_draft(cover_letter="v1")
            draft_v2 = _make_draft(cover_letter="v2")

            service.save_vsa_draft_to_legacy_storage(draft_v1, {"id": "r1"})
            service.save_vsa_draft_to_legacy_storage(draft_v2, {"id": "r1"})
            conn.commit()

            # Only one row for (r1, 1).
            assert facade.application_drafts.count_total() == 1
            saved = facade.application_drafts.get_by_resume_vacancy("r1", 1)
            assert saved is not None
            assert saved.cover_letter == "v2"
        finally:
            conn.close()

    def test_falls_back_to_in_memory_draft_when_re_read_fails(self) -> None:
        """If the post-save re-read raises, the shim returns the
        in-memory ``legacy_draft`` so the caller still sees a non-None
        value (matches the legacy ``_save_vsa_draft_to_legacy_storage``
        contract)."""

        class _ExplodingDrafts:
            def save(self, _draft: Any) -> None:
                from job_bot._legacy_compat.storage.repositories.errors import (
                    RepositoryError,
                )

                raise RepositoryError("save boom")

            def get_by_resume_vacancy(self, _r: str, _v: int) -> Any:
                from job_bot._legacy_compat.storage.repositories.errors import (
                    RepositoryError,
                )

                raise RepositoryError("read boom")

        conn, facade = _make_facade()
        try:
            facade.application_drafts = _ExplodingDrafts()  # type: ignore[assignment]
            service = DraftPersisterService(storage=facade)
            draft = _make_draft()

            saved = service.save_vsa_draft_to_legacy_storage(
                draft, {"id": "r1"}
            )

            # Fallback returns the in-memory legacy_draft (id is None
            # because the legacy facade never assigned one).
            assert saved is not None
            assert saved.id is None
            assert saved.resume_id == "r1"
            assert saved.vacancy_id == 1
        finally:
            conn.close()


# ─── Constructor wiring ────────────────────────────────────────────


class TestConstructor:
    """``__init__`` records ``storage`` / ``clock`` / ``progress_callback``."""

    def test_required_storage_only(self) -> None:
        facade = _make_facade()[1]
        service = DraftPersisterService(storage=facade)
        assert service.storage is facade
        assert service.clock is None
        assert service.progress_callback is None
