"""Tests for :class:`ProfileLoaderService` (issue #147).

Covers the per-phase service that owns the profile-loading + resume
fetching parts of the legacy
``PrepareVacanciesUseCase._load_profiles`` +
``PrepareVacanciesUseCase._fetch_published_resumes`` pair.

Strategy
--------

* **Storage** — a real :class:`job_bot._legacy_compat.storage.facade.StorageFacade`
  against an in-memory ``:memory:`` SQLite connection (initialised
  via ``init_db``). This proves the service integrates with the same
  facade the legacy use case / VSA slice pass to it.
* **API client** — a tiny fake (``_FakeApi``) that records calls and
  returns canned responses for ``GET /resumes/mine``. No
  ``unittest.mock.Mock`` for in-process test doubles.
* **Search profile model** — uses the real
  :class:`job_bot._legacy_compat.storage.models.search_profile.SearchProfileModel`
  so the facade's ``save``/``get``/``find_enabled`` paths are
  exercised end-to-end.

The tests cover:

* explicit ``profile_id`` resolves to a single profile;
* explicit ``profile_id`` returns ``[]`` when the profile is missing;
* explicit ``profile_id`` still returns a disabled profile (with a
  progress_callback notification);
* ``profile_id=None`` returns ``list(find_enabled())``;
* ``fetch_published_resumes`` filters by ``status.id == "published"``;
* ``fetch_published_resumes(dry_run=True)`` skips the
  ``storage.resumes.save_batch`` call.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from job_bot._legacy_compat.storage.facade import StorageFacade
from job_bot._legacy_compat.storage.models.search_profile import (
    SearchProfileModel,
)
from job_bot._legacy_compat.storage.utils import init_db
from job_bot.application_prep.services.profile_loader import (
    ProfileLoaderService,
)


class _FakeApi:
    """Tiny in-process HH API fake.

    Records every ``GET /resumes/mine`` call and returns the canned
    ``resumes`` list. No external dependencies, no Mock.
    """

    def __init__(self, resumes: list[dict[str, Any]]) -> None:
        self._resumes = resumes
        self.calls: list[str] = []

    def get(self, endpoint: str, params: Any = None) -> dict[str, Any]:
        self.calls.append(endpoint)
        if endpoint == "/resumes/mine":
            return {"items": list(self._resumes)}
        return {"items": []}


def _make_facade() -> tuple[sqlite3.Connection, StorageFacade]:
    """Return ``(conn, facade)`` for an in-memory legacy schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn, StorageFacade(conn)


def _profile(
    id_: str = "p1",
    *,
    resume_id: str = "r1",
    enabled: bool = True,
) -> SearchProfileModel:
    return SearchProfileModel(
        id=id_,
        name=id_,
        resume_id=resume_id,
        enabled=enabled,
    )


def _resume(id_: str = "r1", *, status: str = "published") -> dict[str, Any]:
    return {
        "id": id_,
        "title": "Backend",
        "status": {"id": status},
        "alternate_url": f"https://hh.ru/resume/{id_}",
    }


# ─── load_profiles ─────────────────────────────────────────────────


class TestLoadProfiles:
    """``load_profiles(profile_id)`` returns the per-run search profile set."""

    def test_explicit_profile_id_returns_single_profile(self) -> None:
        """``--search-profile=p1`` returns ``[p1]``."""
        conn, facade = _make_facade()
        try:
            facade.search_profiles.save(_profile("p1", enabled=False))
            conn.commit()

            api = _FakeApi(resumes=[])
            service = ProfileLoaderService(api_client=api, storage=facade)

            profiles = service.load_profiles("p1")

            assert len(profiles) == 1
            assert profiles[0].id == "p1"
            # A disabled profile is still returned (the user explicitly
            # asked for it). The service logs a warning and notifies
            # via progress_callback; we don't assert on those here.
        finally:
            conn.close()

    def test_explicit_profile_id_missing_returns_empty(self) -> None:
        """Unknown ``--search-profile=`` returns ``[]`` (logged warning)."""
        conn, facade = _make_facade()
        try:
            api = _FakeApi(resumes=[])
            service = ProfileLoaderService(api_client=api, storage=facade)

            profiles = service.load_profiles("missing")

            assert profiles == []
        finally:
            conn.close()

    def test_explicit_profile_id_invokes_progress_callback_for_disabled(
        self,
    ) -> None:
        """A disabled profile triggers a progress_callback notification
        (the legacy use case surfaces this to UI / Telegram)."""
        conn, facade = _make_facade()
        try:
            facade.search_profiles.save(_profile("p1", enabled=False))
            conn.commit()

            api = _FakeApi(resumes=[])
            messages: list[str] = []

            def cb(msg: str) -> None:
                messages.append(msg)

            service = ProfileLoaderService(
                api_client=api, storage=facade, progress_callback=cb
            )

            profiles = service.load_profiles("p1")

            assert [p.id for p in profiles] == ["p1"]
            assert any("выключен" in m for m in messages), messages
        finally:
            conn.close()

    def test_none_profile_id_returns_enabled_profiles(self) -> None:
        """``profile_id=None`` returns all enabled profiles."""
        conn, facade = _make_facade()
        try:
            facade.search_profiles.save(_profile("p1", enabled=True))
            facade.search_profiles.save(_profile("p2", enabled=False))
            facade.search_profiles.save(_profile("p3", enabled=True))
            conn.commit()

            api = _FakeApi(resumes=[])
            service = ProfileLoaderService(api_client=api, storage=facade)

            profiles = service.load_profiles(None)

            ids = {p.id for p in profiles}
            assert ids == {"p1", "p3"}
        finally:
            conn.close()

    def test_progress_callback_exception_is_logged_not_raised(self) -> None:
        """A raising progress_callback doesn't break the service."""

        def bad_cb(_msg: str) -> None:
            raise RuntimeError("cb boom")

        conn, facade = _make_facade()
        try:
            facade.search_profiles.save(_profile("p1", enabled=False))
            conn.commit()

            api = _FakeApi(resumes=[])
            service = ProfileLoaderService(
                api_client=api, storage=facade, progress_callback=bad_cb
            )

            profiles = service.load_profiles("p1")

            assert [p.id for p in profiles] == ["p1"]
        finally:
            conn.close()


# ─── fetch_published_resumes ────────────────────────────────────────


class TestFetchPublishedResumes:
    """``fetch_published_resumes(dry_run)`` returns the published subset."""

    def test_returns_only_published_resumes(self) -> None:
        conn, facade = _make_facade()
        try:
            api = _FakeApi(
                resumes=[
                    _resume("r1", status="published"),
                    _resume("r2", status="not_published"),
                    _resume("r3", status="published"),
                ]
            )
            service = ProfileLoaderService(api_client=api, storage=facade)

            published = service.fetch_published_resumes()

            ids = {r["id"] for r in published}
            assert ids == {"r1", "r3"}
        finally:
            conn.close()

    def test_persists_full_batch_when_not_dry_run(self) -> None:
        """Without ``dry_run`` the full resume batch is written to
        ``storage.resumes`` (not just the published subset) so the
        user can see drafts in the UI."""
        conn, facade = _make_facade()
        try:
            api = _FakeApi(
                resumes=[
                    _resume("r1", status="published"),
                    _resume("r2", status="not_published"),
                ]
            )
            service = ProfileLoaderService(api_client=api, storage=facade)

            service.fetch_published_resumes()
            conn.commit()

            # All 2 rows persisted (filter happens after save_batch).
            rows = list(facade.resumes.find())
            assert len(rows) == 2
        finally:
            conn.close()

    def test_skips_storage_write_when_dry_run(self) -> None:
        """``dry_run=True`` short-circuits the ``save_batch`` call."""
        conn, facade = _make_facade()
        try:
            api = _FakeApi(resumes=[_resume("r1", status="published")])
            service = ProfileLoaderService(api_client=api, storage=facade)

            published = service.fetch_published_resumes(dry_run=True)
            conn.commit()

            assert [r["id"] for r in published] == ["r1"]
            assert list(facade.resumes.find()) == []
        finally:
            conn.close()

    def test_handles_empty_resumes_list(self) -> None:
        """Empty ``/resumes/mine`` returns ``[]`` and doesn't crash."""
        conn, facade = _make_facade()
        try:
            api = _FakeApi(resumes=[])
            service = ProfileLoaderService(api_client=api, storage=facade)

            published = service.fetch_published_resumes()

            assert published == []
        finally:
            conn.close()

    def test_storage_save_batch_failure_is_logged_not_raised(self) -> None:
        """A ``RepositoryError`` from ``save_batch`` is logged at DEBUG
        and the service still returns the published subset (matches the
        legacy use case's permissive contract)."""
        conn, facade = _make_facade()
        try:
            api = _FakeApi(resumes=[_resume("r1", status="published")])

            class _ExplodingResumeRepo:
                def save_batch(self, _items: list[dict[str, Any]]) -> None:
                    from job_bot._legacy_compat.storage.repositories.errors import (
                        RepositoryError,
                    )

                    raise RepositoryError("boom")

            facade.resumes = _ExplodingResumeRepo()  # type: ignore[assignment]
            service = ProfileLoaderService(api_client=api, storage=facade)

            published = service.fetch_published_resumes()

            assert [r["id"] for r in published] == ["r1"]
        finally:
            conn.close()


# ─── API call wiring ────────────────────────────────────────────────


class TestApiWiring:
    """``GET /resumes/mine`` is the only endpoint touched by this service."""

    def test_resumes_mine_is_called_once(self) -> None:
        conn, facade = _make_facade()
        try:
            api = _FakeApi(resumes=[])
            service = ProfileLoaderService(api_client=api, storage=facade)

            service.fetch_published_resumes()
            service.fetch_published_resumes()

            assert api.calls.count("/resumes/mine") == 2
        finally:
            conn.close()
