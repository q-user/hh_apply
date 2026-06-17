"""Legacy ``StorageFacade`` moved from ``hh_applicant_tool.storage.facade``.

The pre-VSA :class:`StorageFacade` aggregates all 13 legacy repositories
plus the ``init_db`` schema bootstrap. After issue #158 the
``hh_applicant_tool`` distribution package is deleted; the
:class:`StorageFacade` lives on at the same import path inside
``job_bot._legacy_compat.storage`` so the 5 test files that consume it
(test_application_drafts, test_apply_jobs, test_review_flow,
test_search_profiles, test_telegram_bot, test_use_case_with_ports) and
the VSA ``StorageFacade`` in :mod:`job_bot.shared.storage.facade` keep
working unchanged.

The class is byte-for-byte compatible with the legacy one: the same
13 lazy ``repository`` properties, the same ``create()`` classmethod.
The only observable change is the import path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from job_bot._legacy_compat.storage.repositories.application_drafts import (
    ApplicationDraftsRepository,
)
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
from job_bot._legacy_compat.storage.repositories.search_profiles import (
    SearchProfilesRepository,
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
from job_bot._legacy_compat.storage.repositories.vacancies import (
    VacanciesRepository,
)
from job_bot._legacy_compat.storage.utils import init_db


class StorageFacade:
    """Единая точка доступа к persistence-слою."""

    def __init__(self, conn: sqlite3.Connection):
        init_db(conn)
        self.application_drafts = ApplicationDraftsRepository(conn)
        self.application_test_answers = ApplicationTestAnswersRepository(conn)
        self.apply_jobs = ApplyJobsRepository(conn)
        self.employer_sites = EmployerSitesRepository(conn)
        self.employers = EmployersRepository(conn)
        self.negotiations = NegotiationRepository(conn)
        self.resumes = ResumesRepository(conn)
        self.search_profiles = SearchProfilesRepository(conn)
        self.settings = SettingsRepository(conn)
        self.skipped_vacancies = SkippedVacanciesRepository(conn)
        self.telegram_sessions = TelegramSessionsRepository(conn)
        self.vacancies = VacanciesRepository(conn)
        self.vacancy_contacts = VacancyContactsRepository(conn)

    @classmethod
    def create(cls, db_path: str | Path) -> "StorageFacade":
        """Factory to satisfy :class:`StoragePort.create`.

        Issue #56 followup: the slice handlers (and tests) consume a
        ``StoragePort``; this classmethod lets ``StorageFacade`` be
        used as a drop-in implementation. Opens a fresh SQLite
        connection (caller owns it).
        """
        from pathlib import Path as _P

        path = _P(db_path) if not isinstance(db_path, _P) else db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        return cls(conn)
