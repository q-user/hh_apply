"""Use case: ``prepare-vacancies`` (issue #5) — slim orchestrator.

Подготовка черновиков откликов (draft + cover letter + test answers) для
search-профилей из БД — **БЕЗ отправки откликов** на hh.ru. Используется
CLI-командой ``hh-applicant-tool prepare-vacancies`` и будет переиспользоваться
Telegram-ботом (issue #7-9) и apply-worker'ом (issue #10).

Issue #147: this module is a thin orchestrator. The 4 per-phase
helpers split out into :mod:`job_bot.application_prep.services` are:

* :class:`ProfileLoaderService` — load profiles + resumes;
* :class:`VacancyIterationService` — search loop, skip policy, merge;
* :class:`AiFilterService` — per-profile AI filter construction;
* :class:`DraftPersisterService` — vacancy / employer / draft / skip persistence.

The per-profile → per-vacancy orchestration that ran in the original
989-LOC use case is now in
:class:`job_bot.application_prep.services.legacy_prepare_pipeline.LegacyPreparePipeline`
(only used when the VSA slice is **not** wired in — the legacy
fallback path, exercised by ``tests/test_prepare_vacancies.py``).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, cast

import requests

from job_bot._legacy_compat.storage.models.search_profile import (
    SearchProfileModel,
)
from job_bot.application_prep.models.command import PrepareVacanciesCommand
from job_bot.application_prep.models.result import PrepareVacanciesResult
from job_bot.application_prep.services.ai_filter import AiFilterService
from job_bot.application_prep.services.draft_persister import (
    DraftPersisterService,
)
from job_bot.application_prep.services.legacy_prepare_pipeline import (
    LegacyPreparePipeline,
)
from job_bot.application_prep.services.profile_loader import (
    ProfileLoaderService,
)
from job_bot.application_prep.services.vacancy_iteration import (
    VacancyIterationService,
)
from job_bot.application_prep.slice import (
    ApplicationPrepSlice,
    PreparePipelineContext,
    PreparePipelineStats,
)

if TYPE_CHECKING:
    from job_bot.application_prep.handlers.application_handler import (
        ApplicationHandler,
    )
    from job_bot.application_prep.handlers.cover_letter_handler import (
        CoverLetterHandler,
    )
    from job_bot.application_prep.handlers.relevance_handler import (
        RelevanceHandler,
    )
    from job_bot.shared.ports import CancellationToken, Clock
    from job_bot.shared.storage.database import Database

logger = logging.getLogger(__package__)

ProgressCallback = Callable[[str], None]


class PrepareVacanciesUseCase:
    """Оркестратор подготовки черновиков откликов (issue #5, slimmed in #147).

    Public surface is preserved for backward compatibility with the
    ``tests/test_prepare_vacancies.py`` suite and the
    :class:`hh_applicant_tool.container.AppContainer` wiring:

    * constructor signature is unchanged;
    * :meth:`execute` is unchanged;
    * the VSA bridge path (``application_prep_slice``) still delegates
      to :meth:`ApplicationPrepSlice.run_prepare_pipeline`.

    What changed: the per-phase helpers moved into 4 services under
    :mod:`job_bot.application_prep.services`; the per-profile →
    per-vacancy orchestration moved to
    :class:`LegacyPreparePipeline`. The 3 ``_build_*_handler``
    factories and the ``_save_vsa_draft_to_legacy_storage`` shim are
    gone (the shim lives in :class:`DraftPersisterService`).
    """

    def __init__(
        self,
        api_client: Any,
        session: requests.Session,
        storage: Any,
        cover_letter_ai: Any,
        vacancy_filter_ai_factory: Callable[[str], Any] | None,
        *,
        test_ai: Any = None,
        letter_template: str | None = None,
        # Phase 2 ports (optional, backward compatible)
        cancellation: "CancellationToken | None" = None,
        clock: "Clock | None" = None,
        # VSA wiring (issues #54, #90, #142).
        vacancy_search_service_factory: Any = None,
        application_prep_service_factory: Any = None,
        application_prep_slice: ApplicationPrepSlice | None = None,
        relevance_handler: "RelevanceHandler | None" = None,
        cover_letter_handler: "CoverLetterHandler | None" = None,
        application_handler: "ApplicationHandler | None" = None,
        database: "Database | None" = None,
        # 4 per-phase services + legacy pipeline (issue #147).
        profile_loader: "ProfileLoaderService | None" = None,
        vacancy_iteration: "VacancyIterationService | None" = None,
        ai_filter: "AiFilterService | None" = None,
        draft_persister: "DraftPersisterService | None" = None,
        legacy_pipeline: "LegacyPreparePipeline | None" = None,
    ) -> None:
        self.api_client = api_client
        self.session = session
        self.storage = storage
        self.cover_letter_ai = cover_letter_ai
        self.vacancy_filter_ai_factory = vacancy_filter_ai_factory
        self.test_ai = test_ai
        self.letter_template = letter_template
        self._cancellation = cancellation
        self._clock = clock
        self._injected_vacancy_search_service_factory = (
            vacancy_search_service_factory
        )
        self._injected_application_prep_service_factory = (
            application_prep_service_factory
        )
        self._application_prep_slice = application_prep_slice

        # 4 per-phase services (issue #147); default factories build
        # them from the dependencies above.
        self._profile_loader = profile_loader or ProfileLoaderService(
            api_client=api_client, storage=storage
        )
        self._vacancy_iteration = vacancy_iteration or VacancyIterationService(
            api_client=api_client, storage=storage
        )
        self._ai_filter = ai_filter or AiFilterService()
        self._draft_persister = draft_persister or DraftPersisterService(
            storage=storage, clock=clock
        )

        # Legacy per-profile → per-vacancy orchestrator.
        self._legacy_pipeline = legacy_pipeline or LegacyPreparePipeline(
            api_client=api_client,
            storage=storage,
            cover_letter_ai=cover_letter_ai,
            vacancy_filter_ai_factory=vacancy_filter_ai_factory,
            vacancy_iteration=self._vacancy_iteration,
            ai_filter=self._ai_filter,
            draft_persister=self._draft_persister,
            relevance_handler=relevance_handler,
            cover_letter_handler=cover_letter_handler,
            application_handler=application_handler,
            database=database,
            letter_template=letter_template,
        )

        # State, populated in execute().
        self.command: PrepareVacanciesCommand | None = None
        self.cancel_event: threading.Event | None = None
        self.progress_callback: ProgressCallback | None = None

    def execute(
        self,
        command: PrepareVacanciesCommand,
        *,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> PrepareVacanciesResult:
        """Запускает подготовку черновиков (legacy public surface)."""
        self.command = command
        self.cancel_event = cancel_event
        self.progress_callback = progress_callback

        # Push the per-run ``progress_callback`` into the 4 services.
        for service in (
            self._profile_loader,
            self._vacancy_iteration,
            self._draft_persister,
        ):
            service.progress_callback = progress_callback

        result = PrepareVacanciesResult()
        profiles = list(
            self._profile_loader.load_profiles(command.search_profile)
        )
        if not profiles:
            logger.warning(
                "Не найдено активных search-профилей (search_profile=%r)",
                command.search_profile,
            )
            self._notify(
                "Не найдено активных search-профилей "
                f"(search_profile={command.search_profile!r})"
            )
            return result

        resumes = self._profile_loader.fetch_published_resumes(
            dry_run=command.dry_run
        )
        if not resumes:
            logger.warning("У вас нет опубликованных резюме")
            self._notify("⚠️ Нет опубликованных резюме — нечего готовить")
            return result

        resumes_by_id: dict[str, dict[str, Any]] = {
            str(r["id"]): r for r in resumes
        }

        # VSA bridge (issue #90): when a slice is injected, delegate
        # the per-profile → per-vacancy pipeline to it. Otherwise
        # fall through to the legacy pipeline.
        if self._application_prep_slice is not None:
            return self._execute_via_slice(
                profiles=cast(list[SearchProfileModel], profiles),
                resumes_by_id=resumes_by_id,
                command=command,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )

        return self._legacy_pipeline.run(
            profiles=profiles,
            resumes_by_id=resumes_by_id,
            command=command,
            cancel_event=cancel_event,
            cancellation=self._cancellation,
            progress_callback=progress_callback,
        )

    def _execute_via_slice(
        self,
        *,
        profiles: list[SearchProfileModel],
        resumes_by_id: dict[str, dict[str, Any]],
        command: PrepareVacanciesCommand,
        cancel_event: threading.Event | None,
        progress_callback: ProgressCallback | None,
    ) -> PrepareVacanciesResult:
        """Delegate the per-profile pipeline to the VSA slice (issue #90)."""
        if self._application_prep_slice is None:  # pragma: no cover
            raise RuntimeError(
                "_execute_via_slice called without application_prep_slice"
            )
        context = PreparePipelineContext(
            api_client=self.api_client,
            storage=self.storage,
            session=self.session,
            cover_letter_ai=self.cover_letter_ai,
            vacancy_filter_ai_factory=self.vacancy_filter_ai_factory,
            test_ai=self.test_ai,
            letter_template=self.letter_template,
            cancellation=self._cancellation,
            clock=self._clock,
            vacancy_search_service_factory=(
                self._injected_vacancy_search_service_factory
            ),
            application_prep_service_factory=(
                self._injected_application_prep_service_factory
            ),
            progress_callback=progress_callback,
        )
        stats: PreparePipelineStats = (
            self._application_prep_slice.run_prepare_pipeline(
                profiles=profiles,
                resumes_by_id=resumes_by_id,
                context=context,
                dry_run=command.dry_run,
                per_page=command.per_page,
                total_pages=command.total_pages,
                force_message=command.force_message,
                ai_rate_limit=command.ai_rate_limit,
            )
        )
        return PrepareVacanciesResult(
            profiles_processed=stats.profiles_processed,
            vacancies_seen=stats.vacancies_seen,
            prepared=stats.prepared,
            rejected=stats.rejected,
            skipped=stats.skipped,
            test_answers=stats.test_answers,
            failed=stats.failed,
        )

    def _notify(self, *args: Any) -> None:
        message = " ".join(str(a) for a in args)
        print(message)
        if self.progress_callback is not None:
            try:
                self.progress_callback(message)
            except Exception as ex:  # noqa: BLE001
                logger.warning("progress_callback error: %s", ex)
