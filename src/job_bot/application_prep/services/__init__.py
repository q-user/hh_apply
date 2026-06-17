"""Application Preparation services (issue #147).

Each service is a thin VSA wrapper around a phase of the legacy
``PrepareVacanciesUseCase``. The 4 services split the 989-LOC use
case into single-responsibility units that the VSA
``ApplicationPrepSlice`` and the legacy use case can both depend on
via constructor DI.

The split:

* :class:`job_bot.application_prep.services.profile_loader.ProfileLoaderService`
  — wraps ``_load_profiles`` + ``_fetch_published_resumes``.
* :class:`job_bot.application_prep.services.vacancy_iteration.VacancyIterationService`
  — wraps ``_vacancy_search_loop`` + the pure-orchestration part of
  ``_process_vacancy`` (skip policy, full-vacancy fetch, merge).
* :class:`job_bot.application_prep.services.ai_filter.AiFilterService`
  — wraps ``_init_ai_filter`` + the relevance factory; pure (no DB).
* :class:`job_bot.application_prep.services.draft_persister.DraftPersisterService`
  — wraps ``_save_vacancy_to_storage`` + ``_save_skipped_ai_rejected``
  + the ``_save_vsa_draft_to_legacy_storage`` shim.

The use case is reduced to a thin orchestrator (~250 LOC) that calls
these services via constructor DI.
"""

from job_bot.application_prep.services.ai_filter import AiFilterService
from job_bot.application_prep.services.draft_persister import (
    DraftPersisterService,
)
from job_bot.application_prep.services.profile_loader import (
    ProfileLoaderService,
)
from job_bot.application_prep.services.vacancy_iteration import (
    VacancyIterationService,
)

__all__ = [
    "AiFilterService",
    "DraftPersisterService",
    "ProfileLoaderService",
    "VacancyIterationService",
]
