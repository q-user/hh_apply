"""``LegacyPreparePipeline`` — pre-VSA per-profile → per-vacancy loop (issue #147).

This is the leftover legacy per-profile → per-vacancy orchestrator
that the slimmed :class:`hh_applicant_tool.application.use_cases.prepare_vacancies.PrepareVacanciesUseCase`
delegates to when the VSA
:mod:`job_bot.application_prep.slice.ApplicationPrepSlice` is **not**
wired in (the legacy fallback path, exercised by
``tests/test_prepare_vacancies.py``).

Extracted from the 989-LOC ``PrepareVacanciesUseCase`` (issue #147)
so the use case can shrink to a thin adapter (~250 LOC) that:

1. loads profiles + resumes via :class:`ProfileLoaderService`;
2. either delegates the per-profile pipeline to the VSA slice
   (when ``application_prep_slice`` is wired) or to
   :class:`LegacyPreparePipeline` (the legacy path);
3. converts the result back to :class:`PrepareVacanciesResult`.

The legacy pipeline still builds the 3 VSA handlers
(``RelevanceHandler`` / ``CoverLetterHandler`` / ``ApplicationHandler``)
itself because the tests don't pre-build them. The 3 handlers can be
DI'd via the constructor; the default factory builds them from the
injected ``api_client`` / ``cover_letter_ai`` / ``letter_template``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import requests

from job_bot._legacy_compat.storage.repositories.errors import RepositoryError
from job_bot.application_prep.handlers.application_handler import (
    ApplicationHandler,
)
from job_bot.application_prep.handlers.cover_letter_handler import (
    CoverLetterHandler,
)
from job_bot.application_prep.handlers.relevance_handler import (
    RelevanceHandler,
)
from job_bot.application_prep.services.ai_filter import AiFilterService
from job_bot.application_prep.services.draft_persister import (
    DraftPersisterService,
)
from job_bot.application_prep.services.vacancy_iteration import (
    VacancyIterationService,
)
from job_bot.shared.api.errors import ApiError, BadResponse
from job_bot.shared.storage.database import Database

if TYPE_CHECKING:
    from job_bot.application_prep.models.command import PrepareVacanciesCommand
    from job_bot.application_prep.models.result import PrepareVacanciesResult

logger = logging.getLogger(__package__)

ProgressCallback = Callable[[str], None]


def _build_placeholders(resume: dict[str, Any]) -> dict[str, Any]:
    return {
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "resume_hash": resume.get("id") or "",
        "resume_title": resume.get("title") or "",
        "resume_url": resume.get("alternate_url") or "",
        "vacancy_name": "",
        "employer_name": "",
    }


def _profile_search_params(profile: Any) -> dict[str, Any]:
    params: dict[str, Any] = dict(profile.search_params or {})
    params.pop("per_page", None)
    params.pop("total_pages", None)
    return params


def _profile_per_page(profile: Any, default: int) -> int:
    value = (profile.search_params or {}).get("per_page")
    return int(value) if value else default


def _profile_total_pages(profile: Any, default: int) -> int:
    value = (profile.search_params or {}).get("total_pages")
    return int(value) if value else default


def _dry_run_print(vacancy: dict[str, Any], profile: Any) -> None:
    vid = vacancy.get("id")
    alt = vacancy.get("alternate_url") or vid
    has_test = bool(vacancy.get("has_test"))
    print(
        f"[DRY-RUN] Профиль {profile.id}: подготовили бы черновик для "
        f"{alt} (id={vid}, has_test={has_test})"
    )


def _is_cancelled(
    cancel_event: threading.Event | None,
    cancellation: Any,
) -> bool:
    if cancellation is not None:
        return bool(cancellation.is_cancelled)
    return cancel_event is not None and cancel_event.is_set()


def _notify(
    message_parts: tuple[Any, ...],
    progress_callback: ProgressCallback | None,
) -> None:
    message = " ".join(str(a) for a in message_parts)
    print(message)
    if progress_callback is not None:
        try:
            progress_callback(message)
        except Exception as ex:  # noqa: BLE001
            logger.warning("progress_callback error: %s", ex)


class LegacyPreparePipeline:
    """Pre-VSA per-profile → per-vacancy loop (issue #147).

    Owns the legacy per-vacancy pipeline that ran in the original
    989-LOC ``PrepareVacanciesUseCase._process_profile`` /
    ``_process_vacancy`` methods. The pipeline still uses the 4
    services (vacancy iteration, AI filter, draft persister) so
    all per-phase logic stays in one place; the class only owns
    the orchestration glue (build the 3 VSA handlers per profile,
    iterate profiles + vacancies, accumulate stats, dispatch
    notifications).

    Args:
        api_client: HH API client.
        storage: legacy ``StorageFacade`` (also used by the
            ``DraftPersisterService`` for persistence).
        cover_letter_ai: optional AI client for cover letters.
        vacancy_filter_ai_factory: factory for the per-profile
            relevance filter AI client.
        vacancy_iteration: the shared :class:`VacancyIterationService`.
        ai_filter: the shared :class:`AiFilterService`.
        draft_persister: the shared :class:`DraftPersisterService`.
        relevance_handler: optional pre-built
            :class:`RelevanceHandler` (default factory builds one).
        cover_letter_handler: optional pre-built
            :class:`CoverLetterHandler`.
        application_handler: optional pre-built
            :class:`ApplicationHandler`.
        database: optional pre-allocated :class:`Database` for the
            VSA handlers' repos. Defaults to a throwaway temp file.
        letter_template: cover letter template (used by the
            default ``CoverLetterHandler`` factory).
    """

    def __init__(
        self,
        *,
        api_client: Any,
        storage: Any,
        cover_letter_ai: Any,
        vacancy_filter_ai_factory: Any,
        vacancy_iteration: VacancyIterationService,
        ai_filter: AiFilterService,
        draft_persister: DraftPersisterService,
        relevance_handler: RelevanceHandler | None = None,
        cover_letter_handler: CoverLetterHandler | None = None,
        application_handler: ApplicationHandler | None = None,
        database: Database | None = None,
        letter_template: str | None = None,
    ) -> None:
        self.api_client = api_client
        self.storage = storage
        self.cover_letter_ai = cover_letter_ai
        self.vacancy_filter_ai_factory = vacancy_filter_ai_factory
        self.vacancy_iteration = vacancy_iteration
        self.ai_filter = ai_filter
        self.draft_persister = draft_persister
        self._relevance_handler = relevance_handler
        self._cover_letter_handler = cover_letter_handler
        self._application_handler = application_handler
        self._database = database
        self.letter_template = letter_template

    # ─── Public API ──────────────────────────────────────────────

    def run(
        self,
        *,
        profiles: list[Any],
        resumes_by_id: dict[str, dict[str, Any]],
        command: PrepareVacanciesCommand,
        cancel_event: threading.Event | None,
        cancellation: Any,
        progress_callback: ProgressCallback | None,
    ) -> PrepareVacanciesResult:
        """Run the per-profile → per-vacancy loop and accumulate stats.

        Cancellation can be signaled via either a ``threading.Event``
        (legacy path) or a :class:`CancellationToken` port (issue
        #35). Cancellation is checked at every profile and vacancy
        boundary.
        """
        # Imported here (not at module level) to avoid a circular import
        # through ``hh_applicant_tool.application.__init__`` →
        # ``use_cases.prepare_vacancies`` → ``legacy_prepare_pipeline``.
        from job_bot.application_prep.models.result import (
            PrepareVacanciesResult,
        )

        result = PrepareVacanciesResult()
        for profile in profiles:
            if _is_cancelled(cancel_event, cancellation):
                break
            result.profiles_processed += 1
            self._process_profile(
                profile=profile,
                resumes_by_id=resumes_by_id,
                command=command,
                cancel_event=cancel_event,
                cancellation=cancellation,
                progress_callback=progress_callback,
                result=result,
            )
        return result

    # ─── Per-profile / per-vacancy glue ──────────────────────────

    def _process_profile(
        self,
        *,
        profile: Any,
        resumes_by_id: dict[str, dict[str, Any]],
        command: PrepareVacanciesCommand,
        cancel_event: threading.Event | None,
        cancellation: Any,
        progress_callback: ProgressCallback | None,
        result: PrepareVacanciesResult,
    ) -> None:
        resume = resumes_by_id.get(profile.resume_id)
        if resume is None:
            logger.warning(
                "Резюме %s не найдено среди опубликованных — "
                "пропускаю профиль %s",
                profile.resume_id,
                profile.id,
            )
            _notify(
                (
                    f"⚠️ Профиль {profile.id}: резюме {profile.resume_id} "
                    "не опубликовано — пропускаю",
                ),
                progress_callback,
            )
            return

        _notify(
            (
                f"[PROFILE] {profile.id} ({profile.name}) "
                f"→ резюме {resume.get('title')!r}",
            ),
            progress_callback,
        )

        # Build the VSA handlers (or use the pre-built ones).
        relevance = self._relevance_handler or RelevanceHandler(
            database=self._make_vsa_database(),
            api_client=self.api_client,
            ai_client=None,
            relevance_rules=profile.relevance_rules,
        )
        cover_letter = self._cover_letter_handler or CoverLetterHandler(
            database=self._make_vsa_database(),
            api_client=self.api_client,
            ai_client=self.cover_letter_ai,
            template=self.letter_template,
        )
        self.ai_filter.build(
            profile=profile,
            resume=resume,
            relevance_obj=relevance,
            factory=self.vacancy_filter_ai_factory,
            rate_limit=command.ai_rate_limit,
        )
        applications = self._application_handler or ApplicationHandler(
            database=self._make_vsa_database(),
            relevance=relevance,
            cover_letter=cover_letter,
        )

        search_params = _profile_search_params(profile)
        per_page = _profile_per_page(profile, command.per_page)
        total_pages = _profile_total_pages(profile, command.total_pages)

        # Vacancy search loop → VacancyIterationService.
        vacancies: list[dict[str, Any]] = []
        try:
            vacancies = list(
                self.vacancy_iteration.search_vacancies(
                    search_params,
                    per_page=per_page,
                    total_pages=total_pages,
                    resume_id=profile.resume_id,
                )
            )
        except (requests.RequestException, ApiError, BadResponse) as ex:
            logger.exception(
                "Ошибка при поиске вакансий для профиля %s: %s",
                profile.id,
                ex,
            )
            _notify(
                (f"❌ Профиль {profile.id}: ошибка поиска — {ex}",),
                progress_callback,
            )
            return

        _notify(
            (f"[PROFILE] {profile.id}: найдено {len(vacancies)} вакансий",),
            progress_callback,
        )

        for vacancy in vacancies:
            if _is_cancelled(cancel_event, cancellation):
                break
            result.vacancies_seen += 1
            self._process_vacancy(
                vacancy=vacancy,
                profile=profile,
                resume=resume,
                applications=applications,
                command=command,
                progress_callback=progress_callback,
                result=result,
            )

    def _process_vacancy(
        self,
        *,
        vacancy: dict[str, Any],
        profile: Any,
        resume: dict[str, Any],
        applications: ApplicationHandler,
        command: PrepareVacanciesCommand,
        progress_callback: ProgressCallback | None,
        result: PrepareVacanciesResult,
    ) -> None:
        """Подготавливает один черновик (или skip/reject)."""
        vacancy_id = vacancy.get("id")
        alt = vacancy.get("alternate_url") or vacancy_id

        # Skip policy → VacancyIterationService.
        skip_reason = self.vacancy_iteration.skip_reason(
            vacancy, resume.get("id")
        )
        if skip_reason:
            logger.debug("Пропускаю %s: %s", alt, skip_reason)
            _notify((f"[SKIP] {skip_reason}: {alt}",), progress_callback)
            result.skipped += 1
            return

        if command.dry_run:
            _dry_run_print(vacancy, profile)
            result.prepared += 1
            return

        # Full vacancy fetch + merge → VacancyIterationService.
        full_vacancy = self.vacancy_iteration.fetch_full_vacancy(vacancy_id)
        merged = self.vacancy_iteration.merge_vacancy(vacancy, full_vacancy)

        # Persist vacancy + employer → DraftPersisterService.
        self.draft_persister.save_vacancy(merged)
        self.draft_persister.save_employer(merged, api_client=self.api_client)

        # Run the per-vacancy pipeline (AI filter, cover letter, draft).
        try:
            vsa_draft = applications.prepare_draft(
                resume=resume,
                vacancy=merged,
                search_profile_id=profile.id,
                resume_analysis="",
                ai_filter_mode=profile.ai_filter_mode,
                placeholders=_build_placeholders(resume),
                force_message=command.force_message,
                response_url=merged.get("response_url"),
            )
        except (
            RepositoryError,
            requests.RequestException,
            ApiError,
            BadResponse,
        ) as ex:
            logger.exception("Ошибка при подготовке черновика %s: %s", alt, ex)
            _notify((f"[FAIL] {alt}: {ex}",), progress_callback)
            result.failed += 1
            return

        # VSA → legacy storage shim → DraftPersisterService.
        draft = self.draft_persister.save_vsa_draft_to_legacy_storage(
            vsa_draft, resume
        )

        if draft is None:
            result.skipped += 1
            return

        if draft.status == "rejected":
            self.draft_persister.save_skipped_ai_rejected(
                merged, resume.get("id")
            )
            _notify(
                (
                    f"[REJECT] AI отклонил {alt} (score={draft.relevance_score})",
                ),
                progress_callback,
            )
            result.rejected += 1
            return

        result.prepared += 1
        if draft.has_test and draft.test_status == "generated":
            answers = list(
                self.storage.application_test_answers.find_by_draft(draft.id)
            )
            result.test_answers += len(answers)
            _notify(
                (
                    f"[PREPARE] {alt} — draft={draft.id}, "
                    f"test_answers={len(answers)}",
                ),
                progress_callback,
            )
        else:
            extra = " (test=manual_required)" if draft.has_test else ""
            _notify(
                (f"[PREPARE] {alt} — draft={draft.id}{extra}",),
                progress_callback,
            )

    # ─── Helpers ─────────────────────────────────────────────────

    def _make_vsa_database(self) -> Database:
        """Allocate a temp-file :class:`Database` for the VSA handlers."""
        if self._database is not None:
            return self._database
        import tempfile

        tmp_db = tempfile.NamedTemporaryFile(
            prefix="prepare_vacancies_vsa_", suffix=".db", delete=False
        )
        tmp_db.close()
        return Database(tmp_db.name)
