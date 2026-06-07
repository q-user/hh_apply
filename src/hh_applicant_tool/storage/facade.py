from __future__ import annotations

import sqlite3

from .repositories.application_drafts import ApplicationDraftsRepository
from .repositories.application_test_answers import (
    ApplicationTestAnswersRepository,
)
from .repositories.apply_jobs import ApplyJobsRepository
from .repositories.contacts import VacancyContactsRepository
from .repositories.employer_sites import EmployerSitesRepository
from .repositories.employers import EmployersRepository
from .repositories.negotiations import NegotiationRepository
from .repositories.resumes import ResumesRepository
from .repositories.search_profiles import SearchProfilesRepository
from .repositories.settings import SettingsRepository
from .repositories.skipped_vacancies import SkippedVacanciesRepository
from .repositories.telegram_sessions import TelegramSessionsRepository
from .repositories.vacancies import VacanciesRepository
from .utils import init_db


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
