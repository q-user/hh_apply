"""Storage facade - aggregates all repositories for easy access.

Issue #146: fill out the VSA :class:`StorageFacade` from a stub
(``database: Database`` only) into a full composition root that
exposes all 13 legacy repositories the
:class:`hh_applicant_tool.storage.facade.StorageFacade` exposes,
**plus** the 2 new VSA repos that live in
``application_prep/repositories/`` (``cover_letter_repo``,
``relevance_repo``). All 15 repos are exposed as lazy
``@property`` accessors, so constructing a facade is cheap and
``from_db_path("data.sqlite")`` is a one-liner.

Strategy
--------

* The 5 VSA repos (extending :class:`BaseSqliteRepository` and
  declared in their respective slice packages) are wired directly
  with the facade's :class:`Database` instance. Slice import paths
  are preserved (issue #146 architectural rule).
* The 10 legacy repos (extending the legacy
  :class:`hh_applicant_tool.storage.repositories.base.BaseRepository`
  dataclass) are constructed with a long-lived
  :class:`sqlite3.Connection` that the facade opens lazily from the
  same database path. This is the same pattern the
  :class:`job_bot.telegram_bot.slice.TelegramBotSlice._resolve_storage`
  already uses for its long-lived connection. The 4 shim modules
  that import the *legacy* ``StorageFacade`` (issue #146 acceptance
  criterion) keep working untouched — the legacy facade lives at
  ``hh_applicant_tool.storage.facade.StorageFacade`` for one more
  release (removed in #158).

The facade does **not** call
:func:`hh_applicant_tool.storage.utils.init_db` automatically.
Initialising the legacy schema after a VSA repo has already created
the VSA version of a shared table (e.g. ``search_profiles`` with
``enabled``) fails with ``OperationalError: no such column: ...``.
Callers that mix VSA + legacy repos on the same DB must invoke
``init_db`` explicitly, or use the legacy ``StorageFacade`` (which
runs ``init_db`` eagerly) for the legacy side.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .database import Database, validate_db_path


@dataclass
class StorageFacade:
    """Aggregates all 15 repositories for a slice to use.

    Repos are constructed lazily on first access; the facade can be
    instantiated cheaply (e.g. just to type-check a slice's
    dependencies) and only pays for the repos a caller actually
    touches. Each property is also cached after the first access
    so ``facade.vacancies is facade.vacancies`` holds.
    """

    database: Database

    # Lazy cache for all 15 repo instances. ``None`` means "not yet
    # constructed". Using a dict (instead of 15 separate
    # ``_vacancies: VacancyRepository | None`` fields) keeps the
    # dataclass compact and lets ``__init__`` stay implicit.
    _cache: dict[str, Any] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    # Long-lived connection for the 10 legacy repos. Opened on
    # first access to a legacy property; ``None`` until then. The
    # legacy repos hold a :class:`sqlite3.Connection` for the
    # lifetime of the facade (the VSA ``BaseSqliteRepository``
    # opens its own short-lived connections via
    # ``Database.connect()``).
    _legacy_conn: sqlite3.Connection | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Test convenience: accept a raw ``sqlite3.Connection`` as ``database``.

        Some test fixtures yield a ``sqlite3.Connection`` (with the
        legacy schema already initialised) and want to wrap it in a
        :class:`StorageFacade` without going through ``from_db_path``
        (which would create a *separate* in-memory database). When a
        connection is passed, we adopt it as the long-lived legacy
        connection and substitute a fresh in-memory ``Database`` for
        the VSA repos (which will then build on a different schema;
        tests that mix VSA + legacy repos should use ``from_db_path``
        against a temp file instead).

        Issue #145: the per-phase handler tests in
        ``tests/vsa/test_application_submit_handlers_skip.py`` use
        this to share the connection initialised by ``init_db``.
        """
        if isinstance(self.database, sqlite3.Connection):
            self._legacy_conn = self.database
            self.database = Database(":memory:")

    @classmethod
    def from_db_path(cls, db_path: str | Path) -> "StorageFacade":
        """Factory: create a facade from a database path.

        Issue #146: the canonical one-liner
        ``StorageFacade.from_db_path("data.sqlite")`` (also accepts
        ``":memory:"`` for unit tests and ``Path`` instances).
        ``validate_db_path`` is called *before* ``Path(db_path)``
        (issue #78 regression guard) so a stray ``unittest.mock.Mock``
        fails fast instead of being silently coerced to a
        ``Path("MagicMock")`` on disk.
        """
        validate_db_path(db_path)
        return cls(database=Database(db_path))

    def close(self) -> None:
        """Release the long-lived legacy connection (if open).

        Mirrors :meth:`job_bot.telegram_bot.slice.TelegramBotSlice.close`
        so callers can drop the underlying connection deterministically
        (Windows file-locks, integration teardown, etc.). Safe to call
        multiple times.
        """
        conn = self._legacy_conn
        self._legacy_conn = None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    # ─── Legacy connection helper ──────────────────────────────────

    def _legacy_connection(self) -> sqlite3.Connection:
        """Return the long-lived legacy connection, opening it lazily.

        The facade does **not** call
        :func:`hh_applicant_tool.storage.utils.init_db` automatically
        because the VSA and legacy schemas share a few table names
        (``search_profiles``, ``vacancies``, ``application_drafts``)
        with different column sets. Initialising the legacy schema
        after a VSA repo has already created the VSA version fails
        with ``OperationalError: no such column: ...`` (e.g. on the
        ``idx_search_profiles_enabled`` index). Callers that need the
        legacy schema in place must invoke :func:`init_db` explicitly
        (or use the :class:`hh_applicant_tool.storage.facade.StorageFacade`
        legacy facade which does it eagerly on construction -- the 4
        shim modules in issue #146's acceptance criterion do exactly
        that and are unaffected by this design choice).
        """
        if self._legacy_conn is None:
            conn = sqlite3.connect(self.database.path)
            conn.row_factory = sqlite3.Row
            self._legacy_conn = conn
        return self._legacy_conn

    # ─── 5 VSA properties (BaseSqliteRepository subclasses) ────────

    @property
    def search_profiles(self) -> Any:
        """Search profiles repository (VSA)."""
        cached = self._cache.get("search_profiles")
        if cached is not None:
            return cached
        from job_bot.vacancy_search.repositories.search_profile_repo import (
            SearchProfileRepository,
        )

        repo = SearchProfileRepository(self.database)
        self._cache["search_profiles"] = repo
        return repo

    @property
    def vacancies(self) -> Any:
        """Vacancies repository (VSA)."""
        cached = self._cache.get("vacancies")
        if cached is not None:
            return cached
        from job_bot.vacancy_search.repositories.vacancy_repo import (
            VacancyRepository,
        )

        repo = VacancyRepository(self.database)
        self._cache["vacancies"] = repo
        return repo

    @property
    def application_drafts(self) -> Any:
        """Application drafts repository (VSA)."""
        cached = self._cache.get("application_drafts")
        if cached is not None:
            return cached
        from job_bot.application_prep.repositories.application_repo import (
            ApplicationDraftRepository,
        )

        repo = ApplicationDraftRepository(self.database)
        self._cache["application_drafts"] = repo
        return repo

    @property
    def cover_letters(self) -> Any:
        """Cover letters repository (VSA)."""
        cached = self._cache.get("cover_letters")
        if cached is not None:
            return cached
        from job_bot.application_prep.repositories.cover_letter_repo import (
            CoverLetterRepository,
        )

        repo = CoverLetterRepository(self.database)
        self._cache["cover_letters"] = repo
        return repo

    @property
    def relevance_analyses(self) -> Any:
        """Relevance analyses repository (VSA)."""
        cached = self._cache.get("relevance_analyses")
        if cached is not None:
            return cached
        from job_bot.application_prep.repositories.relevance_repo import (
            RelevanceAnalysisRepository,
        )

        repo = RelevanceAnalysisRepository(self.database)
        self._cache["relevance_analyses"] = repo
        return repo

    # ─── 10 legacy properties (legacy BaseRepository subclasses) ───
    #
    # The legacy repos hold a long-lived ``sqlite3.Connection`` (not
    # a ``Database``), so they cannot share the VSA repos'
    # short-lived ``Database.connect()`` connections. The facade
    # opens one persistent connection via ``_legacy_connection()``
    # and shares it across all 10 legacy repos. The 4 shim modules
    # (issue #146 acceptance) that import
    # ``hh_applicant_tool.storage.StorageFacade`` keep working
    # because the *legacy* ``StorageFacade`` is unchanged and
    # continues to live at ``hh_applicant_tool.storage.facade``.

    @property
    def application_test_answers(self) -> Any:
        """Application test answers repository (legacy)."""
        cached = self._cache.get("application_test_answers")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.application_test_answers import (
            ApplicationTestAnswersRepository,
        )

        repo = ApplicationTestAnswersRepository(self._legacy_connection())
        self._cache["application_test_answers"] = repo
        return repo

    @property
    def apply_jobs(self) -> Any:
        """Apply-jobs queue repository (legacy)."""
        cached = self._cache.get("apply_jobs")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.apply_jobs import (
            ApplyJobsRepository,
        )

        repo = ApplyJobsRepository(self._legacy_connection())
        self._cache["apply_jobs"] = repo
        return repo

    @property
    def employer_sites(self) -> Any:
        """Employer sites repository (legacy)."""
        cached = self._cache.get("employer_sites")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.employer_sites import (
            EmployerSitesRepository,
        )

        repo = EmployerSitesRepository(self._legacy_connection())
        self._cache["employer_sites"] = repo
        return repo

    @property
    def employers(self) -> Any:
        """Employers repository (legacy)."""
        cached = self._cache.get("employers")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.employers import (
            EmployersRepository,
        )

        repo = EmployersRepository(self._legacy_connection())
        self._cache["employers"] = repo
        return repo

    @property
    def negotiations(self) -> Any:
        """Negotiations repository (legacy)."""
        cached = self._cache.get("negotiations")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.negotiations import (
            NegotiationRepository,
        )

        repo = NegotiationRepository(self._legacy_connection())
        self._cache["negotiations"] = repo
        return repo

    @property
    def resumes(self) -> Any:
        """Resumes repository (legacy)."""
        cached = self._cache.get("resumes")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.resumes import (
            ResumesRepository,
        )

        repo = ResumesRepository(self._legacy_connection())
        self._cache["resumes"] = repo
        return repo

    @property
    def settings(self) -> Any:
        """Settings repository (legacy)."""
        cached = self._cache.get("settings")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.settings import (
            SettingsRepository,
        )

        repo = SettingsRepository(self._legacy_connection())
        self._cache["settings"] = repo
        return repo

    @property
    def skipped_vacancies(self) -> Any:
        """Skipped vacancies repository (legacy)."""
        cached = self._cache.get("skipped_vacancies")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.skipped_vacancies import (
            SkippedVacanciesRepository,
        )

        repo = SkippedVacanciesRepository(self._legacy_connection())
        self._cache["skipped_vacancies"] = repo
        return repo

    @property
    def telegram_sessions(self) -> Any:
        """Telegram sessions repository (legacy)."""
        cached = self._cache.get("telegram_sessions")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.telegram_sessions import (
            TelegramSessionsRepository,
        )

        repo = TelegramSessionsRepository(self._legacy_connection())
        self._cache["telegram_sessions"] = repo
        return repo

    @property
    def vacancy_contacts(self) -> Any:
        """Vacancy contacts repository (legacy)."""
        cached = self._cache.get("vacancy_contacts")
        if cached is not None:
            return cached
        from job_bot._legacy_compat.storage.repositories.contacts import (
            VacancyContactsRepository,
        )

        repo = VacancyContactsRepository(self._legacy_connection())
        self._cache["vacancy_contacts"] = repo
        return repo


# ─── Module-level factory (kept for back-compat) ──────────────────


def create_storage_facade(db_path: str | Path) -> StorageFacade:
    """Factory function to create a StorageFacade with database.

    Issue #78: validate ``db_path`` *before* ``Path(db_path)`` so a
    ``unittest.mock.Mock`` fails fast instead of being silently coerced
    to its class-name string (``"MagicMock"``) and turned into a real
    filesystem directory.

    Equivalent to :meth:`StorageFacade.from_db_path`; kept as a free
    function for the existing call sites in the slice-handler tests
    (``tests/vsa/test_*_slice.py``) and :mod:`hh_applicant_tool.container`.
    """
    validate_db_path(db_path)
    database = Database(Path(db_path))
    return StorageFacade(database=database)
