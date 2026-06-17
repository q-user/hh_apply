"""Tests for ``StorageFacade`` -- the VSA composition root for storage (issue #146).

Issue #146 expands the VSA :class:`StorageFacade` from a stub with only
``database: Database`` into a full composition root that exposes all
the repositories the legacy :class:`hh_applicant_tool.storage.facade.StorageFacade`
exposes (application_drafts, application_test_answers, apply_jobs,
employer_sites, employers, negotiations, resumes, search_profiles,
settings, skipped_vacancies, telegram_sessions, vacancies,
vacancy_contacts) plus the new VSA repos in
``application_prep/repositories/`` (cover_letter_repo, relevance_repo).

The 5 VSA repos that already exist keep their import paths
(``vacancy_search.repositories.{search_profile_repo, vacancy_repo}``,
``application_prep.repositories.{application_repo, cover_letter_repo,
relevance_repo}``). The 9 missing repos are wired via the legacy
classes under ``hh_applicant_tool.storage.repositories.*`` so the
acceptance criterion (4 shim modules that import the legacy facade keep
working) is preserved for one more release (removed in #158).

These tests cover the factory and lazy-init contract from the issue:

* ``from_db_path``: one-liner factory
  (e.g. ``StorageFacade.from_db_path("data.sqlite")``).
* ``database`` property: returns the wrapped :class:`Database`.
* 15 lazy properties: each returns the correct concrete repo class.
* Lazy initialisation: constructing the facade does not construct any
  repo; only accessing a property triggers that repo's ``__init__``.
* Protocol completeness: the cross-slice :class:`StoragePort` Protocol
  declares all 15 properties (so cross-slice consumers can rely on it).

The number is 15, not 14 as the issue title says: the issue body
explicitly enumerates 13 legacy + 2 new VSA (``cover_letters`` and
``relevance_analyses``) = 15. The "14" in the title and acceptance
criterion is an off-by-one counting error; the body list is the
authoritative spec.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from job_bot.application_prep.repositories.application_repo import (
    ApplicationDraftRepository,
)
from job_bot.application_prep.repositories.cover_letter_repo import (
    CoverLetterRepository,
)
from job_bot.application_prep.repositories.relevance_repo import (
    RelevanceAnalysisRepository,
)
from job_bot.shared.storage.database import Database
from job_bot.shared.storage.facade import StorageFacade
from job_bot.shared.storage.ports import StoragePort
from job_bot.vacancy_search.repositories.search_profile_repo import (
    SearchProfileRepository,
)
from job_bot.vacancy_search.repositories.vacancy_repo import VacancyRepository

# All 15 repo property names exposed by ``StorageFacade``
# (issue #146 body enumerates 13 legacy + 2 new VSA repos).
EXPECTED_REPO_PROPERTIES: tuple[str, ...] = (
    "application_drafts",
    "application_test_answers",
    "apply_jobs",
    "cover_letters",
    "employer_sites",
    "employers",
    "negotiations",
    "relevance_analyses",
    "resumes",
    "search_profiles",
    "settings",
    "skipped_vacancies",
    "telegram_sessions",
    "vacancies",
    "vacancy_contacts",
)

# The 5 VSA-concrete classes (the other 9 are legacy; checked inline).
EXPECTED_VSA_REPO_TYPES: dict[str, type] = {
    "search_profiles": SearchProfileRepository,
    "vacancies": VacancyRepository,
    "application_drafts": ApplicationDraftRepository,
    "cover_letters": CoverLetterRepository,
    "relevance_analyses": RelevanceAnalysisRepository,
}


# ─── Factory / construction ─────────────────────────────────────


class TestStorageFacadeFactory:
    """``StorageFacade.from_db_path(...)`` is the canonical one-liner factory."""

    def test_storage_facade_constructs_with_db_path(
        self, tmp_path: Path
    ) -> None:
        """``StorageFacade.from_db_path(path)`` returns a configured facade.

        Uses a real on-disk path under ``tmp_path`` (issue #146 test plan
        calls for ``tmp_path``; ``:memory:`` would not exercise
        ``Database._db_path.parent.mkdir`` on a relative path).
        """
        db_file = tmp_path / "data.sqlite"
        facade = StorageFacade.from_db_path(db_file)

        assert isinstance(facade, StorageFacade)
        assert isinstance(facade.database, Database)
        # The Database wraps the requested path verbatim (Path() coercion).
        assert Path(facade.database.path) == db_file

    def test_storage_facade_constructs_with_memory_db(self) -> None:
        """``StorageFacade.from_db_path(":memory:")`` works for unit tests.

        Mirrors the issue's test-plan verbatim
        (``StorageFacade.from_db_path(":memory:")``). The Database
        rejects :class:`Mock` paths via :func:`validate_db_path` (issue
        #78); ``":memory:"`` is a real :class:`str` and must be accepted.
        """
        facade = StorageFacade.from_db_path(":memory:")
        assert isinstance(facade, StorageFacade)
        assert isinstance(facade.database, Database)

    def test_storage_facade_constructs_with_str_path(
        self, tmp_path: Path
    ) -> None:
        """A ``str`` path is accepted (the most common call shape)."""
        path = str(tmp_path / "x.sqlite")
        facade = StorageFacade.from_db_path(path)
        assert isinstance(facade, StorageFacade)
        assert isinstance(facade.database, Database)

    def test_storage_facade_rejects_mock_db_path(self) -> None:
        """Regression guard for issue #78: a ``Mock`` path is rejected fast."""
        from unittest.mock import Mock

        with pytest.raises(TypeError, match="db_path must be a real Path"):
            StorageFacade.from_db_path(Mock())


# ─── ``.database`` property ──────────────────────────────────────


class TestStorageFacadeDatabaseProperty:
    """``.database`` returns the :class:`Database` passed at construction time."""

    def test_storage_facade_database_property(self, tmp_path: Path) -> None:
        """``facade.database`` returns the wrapped ``Database``."""
        facade = StorageFacade.from_db_path(tmp_path / "f.sqlite")
        db = facade.database

        assert isinstance(db, Database)
        # Identity: the facade stores the Database by reference, not a copy.
        assert db is facade.database

    def test_storage_facade_accepts_database_directly(
        self, tmp_path: Path
    ) -> None:
        """Direct ``StorageFacade(database=...)`` construction also works.

        ``from_db_path`` is the canonical factory but the dataclass
        constructor stays usable for tests that already have a
        :class:`Database` instance (e.g. shared with the slice).
        """
        db = Database(tmp_path / "f.sqlite")
        facade = StorageFacade(database=db)

        assert facade.database is db


# ─── 15-repo exposure ────────────────────────────────────────────


class TestStorageFacadeExposesAllRepos:
    """Every one of the 15 property names returns the right concrete class.

    Uses ``tmp_path`` (not ``:memory:``) so the legacy schema's
    ``init_db`` can run on a real file-backed connection. The legacy
    repos hold a long-lived ``sqlite3.Connection``, which the facade
    opens lazily on first access to a legacy property.
    """

    @pytest.fixture
    def facade(self, tmp_path: Path) -> Iterator[StorageFacade]:
        """Fresh facade backed by a temp file (cleaned up on teardown)."""
        f = StorageFacade.from_db_path(tmp_path / "facade_all.sqlite")
        try:
            yield f
        finally:
            # Best-effort: drop the facade's cached long-lived connection
            # so the tmp_path file can be unlinked on Windows.
            close = getattr(f, "close", None)
            if callable(close):
                close()

    def test_storage_facade_lists_all_repo_properties(
        self, facade: StorageFacade
    ) -> None:
        """The facade exposes all 15 property names."""
        for name in EXPECTED_REPO_PROPERTIES:
            assert hasattr(facade, name), (
                f"StorageFacade is missing required property {name!r} "
                f"(see issue #146: facade must expose all repos)"
            )

    def test_storage_facade_exposes_vsa_repos_with_vsa_classes(
        self, facade: StorageFacade
    ) -> None:
        """The 5 VSA properties return VSA-concrete classes.

        Issue #146: "VSA repos that already extend BaseSqliteRepository
        must keep their import paths." These are the 5 VSA repos that
        the facade wires up from the existing VSA package locations.
        """
        for prop, expected_cls in EXPECTED_VSA_REPO_TYPES.items():
            repo = getattr(facade, prop)
            assert isinstance(repo, expected_cls), (
                f"facade.{prop} returned {type(repo).__name__}, "
                f"expected {expected_cls.__name__}"
            )

    def test_storage_facade_exposes_legacy_repos_with_legacy_classes(
        self, facade: StorageFacade
    ) -> None:
        """The 9 legacy properties return the legacy concrete classes.

        The 4 shim modules
        (``job_bot.telegram_bot.services.{bot_service,daily_digest_service,review_service}``
        and ``job_bot.application_submit.handlers.job_handler``) consume
        the *legacy* classes (they call ``.save()`` / ``.find()`` / etc.
        from the legacy ``BaseRepository`` API). The facade's contract
        is to return the same legacy instances so those shims work
        without a behavioural change.
        """
        from job_bot._legacy_compat.storage.repositories.application_test_answers import (
            ApplicationTestAnswersRepository,
        )
        from job_bot._legacy_compat.storage.repositories.apply_jobs import (
            ApplyJobsRepository,
        )
        from job_bot._legacy_compat.storage.repositories.contacts import (
            VacancyContactsRepository,
        )
        from job_bot._legacy_compat.storage.repositories.employer_sites import (
            EmployerSitesRepository,
        )
        from job_bot._legacy_compat.storage.repositories.employers import (
            EmployersRepository,
        )
        from job_bot._legacy_compat.storage.repositories.negotiations import (
            NegotiationRepository,
        )
        from job_bot._legacy_compat.storage.repositories.resumes import (
            ResumesRepository,
        )
        from job_bot._legacy_compat.storage.repositories.settings import (
            SettingsRepository,
        )
        from job_bot._legacy_compat.storage.repositories.skipped_vacancies import (
            SkippedVacanciesRepository,
        )
        from job_bot._legacy_compat.storage.repositories.telegram_sessions import (
            TelegramSessionsRepository,
        )

        expected: dict[str, type] = {
            "application_test_answers": ApplicationTestAnswersRepository,
            "apply_jobs": ApplyJobsRepository,
            "employer_sites": EmployerSitesRepository,
            "employers": EmployersRepository,
            "negotiations": NegotiationRepository,
            "resumes": ResumesRepository,
            "settings": SettingsRepository,
            "skipped_vacancies": SkippedVacanciesRepository,
            "telegram_sessions": TelegramSessionsRepository,
            "vacancy_contacts": VacancyContactsRepository,
        }
        for prop, expected_cls in expected.items():
            repo = getattr(facade, prop)
            assert isinstance(repo, expected_cls), (
                f"facade.{prop} returned {type(repo).__name__}, "
                f"expected {expected_cls.__name__}"
            )

    def test_storage_facade_repo_property_is_idempotent(
        self, facade: StorageFacade
    ) -> None:
        """Accessing the same property twice returns the same instance.

        Lazy caching contract: the first access constructs, the second
        returns the cached instance. This also pins the facade's
        promise that the 15 repos are *singletons per facade* (callers
        can rely on identity for memoised slices).
        """
        first = facade.search_profiles
        second = facade.search_profiles
        assert first is second

        first_legacy = facade.negotiations
        second_legacy = facade.negotiations
        assert first_legacy is second_legacy


# ─── Lazy init ───────────────────────────────────────────────────


class TestStorageFacadeLazyInit:
    """Constructing a facade does NOT construct any of the 15 repos.

    The issue's test plan: "repo properties are lazy (constructing the
    facade does not construct every repo; accessing ``facade.vacancies``
    constructs just that one)". We use ``__init__`` spy patches to
    observe the construction count.
    """

    def test_storage_facade_lazy_init_does_not_construct_vacancies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Constructing the facade does not call ``VacancyRepository.__init__``."""
        from job_bot.vacancy_search.repositories import vacancy_repo

        original_init = vacancy_repo.VacancyRepository.__init__
        call_count = {"n": 0}

        def spy_init(self: object, *args: object, **kwargs: object) -> None:
            call_count["n"] += 1
            original_init(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            vacancy_repo.VacancyRepository, "__init__", spy_init
        )

        facade = StorageFacade.from_db_path(tmp_path / "lazy.sqlite")
        # Constructing the facade must not have constructed any repo.
        assert call_count["n"] == 0, (
            f"StorageFacade construction eagerly built {call_count['n']} "
            f"repo(s); issue #146 requires lazy properties"
        )

        # Accessing ``.vacancies`` constructs exactly one repo.
        _ = facade.vacancies
        assert call_count["n"] == 1

        # Accessing it again does not reconstruct (idempotent / cached).
        _ = facade.vacancies
        assert call_count["n"] == 1

    def test_storage_facade_lazy_init_does_not_construct_legacy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Constructing the facade does not call any legacy ``__init__``.

        Patches the 9 legacy repo classes (via their
        ``__init__`` ``call_count``) and verifies that merely
        constructing the facade does not invoke any of them. The facade
        should only open the legacy connection + run ``init_db`` when
        the first legacy property is accessed.
        """
        from job_bot._legacy_compat.storage.repositories import (
            application_test_answers,
            apply_jobs,
            contacts,
            employer_sites,
            employers,
            negotiations,
            resumes,
            settings,
            skipped_vacancies,
            telegram_sessions,
        )

        legacy_classes: dict[str, type] = {
            "application_test_answers": (
                application_test_answers.ApplicationTestAnswersRepository
            ),
            "apply_jobs": apply_jobs.ApplyJobsRepository,
            "vacancy_contacts": contacts.VacancyContactsRepository,
            "employer_sites": employer_sites.EmployerSitesRepository,
            "employers": employers.EmployersRepository,
            "negotiations": negotiations.NegotiationRepository,
            "resumes": resumes.ResumesRepository,
            "settings": settings.SettingsRepository,
            "skipped_vacancies": (skipped_vacancies.SkippedVacanciesRepository),
            "telegram_sessions": (telegram_sessions.TelegramSessionsRepository),
        }

        counters: dict[str, int] = {k: 0 for k in legacy_classes}
        originals = {k: cls.__init__ for k, cls in legacy_classes.items()}

        def make_spy(name: str, original: object) -> object:
            def spy(self: object, *args: object, **kwargs: object) -> None:
                counters[name] += 1
                original(self, *args, **kwargs)  # type: ignore[operator]

            return spy

        for name, cls in legacy_classes.items():
            monkeypatch.setattr(
                cls, "__init__", make_spy(name, originals[name])
            )

        facade = StorageFacade.from_db_path(tmp_path / "lazy2.sqlite")
        for name, count in counters.items():
            assert count == 0, (
                f"StorageFacade construction eagerly constructed "
                f"legacy {name!r} ({count} times); should be lazy"
            )

        # Accessing ``.negotiations`` constructs exactly one legacy repo.
        _ = facade.negotiations
        assert counters["negotiations"] == 1
        for name, count in counters.items():
            if name == "negotiations":
                continue
            assert count == 0, (
                f"Accessing facade.negotiations unexpectedly constructed "
                f"legacy {name!r} ({count} times)"
            )


# ─── Protocol completeness ───────────────────────────────────────


class TestStoragePortProtocol:
    """``StoragePort`` Protocol declares all 15 repo properties.

    Cross-slice consumers depend on the Protocol for structural
    type-checking. The Protocol must list every repo the facade
    exposes so ``mypy --strict`` flags a missing implementation on
    any future port adapter.
    """

    def test_storage_port_protocol_declares_all_properties(self) -> None:
        """The ``StoragePort`` Protocol has all 15 properties declared.

        Properties on a :class:`typing.Protocol` class do **not** show
        up in ``__annotations__`` -- the return-type annotation goes
        to ``<property>.fget.__annotations__['return']`` instead. So
        the test asserts both:

        * ``hasattr(StoragePort, name)`` -- the property is declared;
        * the property's ``fget`` has a ``return`` annotation
          (structural typing requires a return type on a Protocol
          method/property).
        """
        for name in EXPECTED_REPO_PROPERTIES:
            assert hasattr(StoragePort, name), (
                f"StoragePort Protocol is missing required property "
                f"{name!r} (issue #146: protocol must declare all repos)"
            )
            attr = getattr(StoragePort, name)
            # A Protocol-declared property is a ``property`` instance
            # whose ``fget`` carries the return-type annotation.
            fget = getattr(attr, "fget", None)
            assert fget is not None, (
                f"StoragePort.{name} is not a property descriptor"
            )
            fget_annotations = getattr(fget, "__annotations__", {}) or {}
            assert "return" in fget_annotations, (
                f"StoragePort.{name} property has no return-type "
                f"annotation in its fget; the Protocol is incomplete"
            )

    def test_storage_port_protocol_declares_from_db_path(self) -> None:
        """The ``StoragePort`` Protocol declares the ``from_db_path`` factory."""
        # The factory is a classmethod; on a Protocol it shows up as
        # an unbound method whose underlying ``__func__`` is the
        # classmethod descriptor.
        assert hasattr(StoragePort, "from_db_path")
        attr = StoragePort.from_db_path
        underlying = getattr(attr, "__func__", attr)
        annotations = getattr(underlying, "__annotations__", {}) or {}
        assert "db_path" in annotations, (
            "StoragePort.from_db_path must annotate its 'db_path' "
            "argument (Protocol completeness, issue #146)"
        )

    def test_storage_facade_satisfies_storage_port(
        self, tmp_path: Path
    ) -> None:
        """The concrete :class:`StorageFacade` satisfies :class:`StoragePort`.

        ``runtime_checkable`` is not applied to the Protocol (it's an
        implementation detail of the Protocol; mypy checks it
        structurally). For runtime confidence we exercise the explicit
        set of properties on a real facade.
        """
        facade = StorageFacade.from_db_path(tmp_path / "port.sqlite")
        for name in EXPECTED_REPO_PROPERTIES:
            assert hasattr(facade, name)
