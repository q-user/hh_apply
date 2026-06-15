"""Application Preparation slice - main entry point and factory."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Iterable, cast

import requests

from job_bot.application_prep.handlers.application_handler import (
    ApplicationHandler,
)
from job_bot.application_prep.handlers.cover_letter_handler import (
    CoverLetterHandler,
)
from job_bot.application_prep.handlers.relevance_handler import (
    RelevanceHandler,
)
from job_bot.application_prep.ports.application_port import ApplicationPort
from job_bot.application_prep.ports.cover_letter_port import CoverLetterPort
from job_bot.application_prep.ports.relevance_port import (
    RelevancePort,
    RelevanceStoragePort,
)
from job_bot.application_prep.utils import build_filter_ai_client
from job_bot.shared.api.client import HHApiClient, HHApiConfig
from job_bot.shared.config.settings import Settings
from job_bot.shared.storage.database import Database, create_database

if TYPE_CHECKING:
    from job_bot.shared.ai.client import AIClient, AIConfig
    from job_bot.vacancy_search.ports.vacancy_port import VacancyPort

logger = logging.getLogger(__name__)


# ─── Pipeline context & stats (issue #90) ───────────────────────


@dataclass
class PreparePipelineContext:
    """Bundle of dependencies for :meth:`ApplicationPrepSlice.run_prepare_pipeline`.

    The VSA slice is the top-level orchestrator for the prepare-vacancies
    pipeline (issue #90). The pipeline still uses the legacy
    ``api_client``, ``StorageFacade``, and ``requests.Session`` for I/O
    (until those are migrated to VSA-native equivalents in follow-up
    issues). The context object groups these dependencies to keep the
    slice method signature manageable and to avoid growing the slice
    constructor with per-run state.
    """

    api_client: Any
    storage: Any
    session: Any
    cover_letter_ai: Any
    vacancy_filter_ai_factory: Callable[[str], Any] | None
    test_ai: Any | None = None
    letter_template: str | None = None
    cancellation: Any | None = None
    clock: Any | None = None
    vacancy_search_service_factory: Any = None
    application_prep_service_factory: Any | None = None
    progress_callback: Callable[[str], None] | None = None


@dataclass
class PreparePipelineStats:
    """Per-profile / per-vacancy statistics from a prepare pipeline run.

    Field names match
    :class:`hh_applicant_tool.application.dto.PrepareVacanciesResult`
    so the legacy module can convert trivially. Kept as a separate
    dataclass so the VSA slice does not import from
    ``hh_applicant_tool``.
    """

    profiles_processed: int = 0
    vacancies_seen: int = 0
    prepared: int = 0
    rejected: int = 0
    skipped: int = 0
    test_answers: int = 0
    failed: int = 0


class ApplicationPrepSlice:
    """Application Preparation slice - encapsulates all draft preparation functionality.

    Public surface:
    - relevance: AI-based relevance filtering
    - cover_letters: Cover letter generation and persistence
    - applications: Orchestrated draft preparation
    """

    def __init__(
        self,
        database: Database,
        api_client: HHApiClient | None = None,
        ai_client: "AIClient | None" = None,
        *,
        relevance_rules: dict[str, Any] | None = None,
        ai_failure_mode: str = "permissive",
        cover_letter_template: str | None = None,
        vacancy_port: "VacancyPort | None" = None,
    ) -> None:
        self._database = database
        self._api_client = api_client or HHApiClient()
        self._ai_client = ai_client

        # Create handlers
        self._relevance_handler = RelevanceHandler(
            database,
            api_client=self._api_client,
            ai_client=self._ai_client,
            relevance_rules=relevance_rules,
            ai_failure_mode=ai_failure_mode,
        )
        self._cover_letter_handler = CoverLetterHandler(
            database,
            api_client=self._api_client,
            ai_client=self._ai_client,
            template=cover_letter_template,
            vacancy_port=vacancy_port,
        )
        self._application_handler = ApplicationHandler(
            database,
            relevance=self._relevance_handler,
            cover_letter=self._cover_letter_handler,
        )

    # ─── Top-level prepare-vacancies orchestrator (issue #90) ─────────────────

    def run_prepare_pipeline(
        self,
        *,
        profiles: Iterable[Any],
        resumes_by_id: dict[str, dict[str, Any]],
        context: PreparePipelineContext,
        dry_run: bool = False,
        per_page: int = 100,
        total_pages: int = 20,
        force_message: bool = True,
        ai_rate_limit: int = 40,
    ) -> PreparePipelineStats:
        """Run the full prepare-vacancies pipeline (issue #90).

        Top-level VSA orchestrator extracted from the legacy
        :class:`hh_applicant_tool.application.use_cases.prepare_vacancies.PrepareVacanciesUseCase`.
        Iterates search profiles, searches vacancies, runs the per-vacancy
        AI filter + cover letter + draft save, and accumulates statistics.

        The legacy use case's :meth:`execute` delegates to this method
        after loading profiles and fetching resumes (its own
        responsibilities). The slice owns the per-profile → per-vacancy
        pipeline; the legacy use case retains profile loading and
        resume fetching as compatibility shims.

        Args:
            profiles: iterable of search profile objects (duck-typed;
                reads ``id``, ``name``, ``resume_id``, ``enabled``,
                ``ai_filter_mode``, ``relevance_rules``, ``search_params``).
            resumes_by_id: mapping ``resume_id -> resume dict``.
            context: legacy infrastructure dependencies (api_client,
                storage, session, AI factories, cancellation, clock,
                factories, progress callback).
            dry_run: don't write to DB (still call HH API + AI).
            per_page: vacancies per page.
            total_pages: max pages to search.
            force_message: always generate cover letter.
            ai_rate_limit: AI request rate limit.

        Returns:
            :class:`PreparePipelineStats` with per-run counts.
        """
        stats = PreparePipelineStats()
        for profile in profiles:
            if self._pipeline_is_cancelled(context.cancellation):
                break
            stats.profiles_processed += 1
            self._pipeline_process_profile(
                profile=profile,
                resumes_by_id=resumes_by_id,
                context=context,
                dry_run=dry_run,
                per_page=per_page,
                total_pages=total_pages,
                force_message=force_message,
                ai_rate_limit=ai_rate_limit,
                stats=stats,
            )
        return stats

    # ─── Private pipeline helpers (extracted from legacy use case) ──────────────────

    def _pipeline_process_profile(
        self,
        *,
        profile: Any,
        resumes_by_id: dict[str, dict[str, Any]],
        context: PreparePipelineContext,
        stats: PreparePipelineStats,
        dry_run: bool,
        per_page: int,
        total_pages: int,
        force_message: bool,
        ai_rate_limit: int,
    ) -> None:
        """Prepare drafts for a single search profile."""
        resume = resumes_by_id.get(getattr(profile, "resume_id", ""))
        if resume is None:
            profile_id = getattr(profile, "id", "?")
            self._pipeline_notify(
                f"⚠️ Профиль {profile_id}: "
                f"резюме {getattr(profile, 'resume_id', '?')} "
                "не опубликовано — пропускаю",
                context.progress_callback,
            )
            return

        profile_id = getattr(profile, "id", "?")
        profile_name = getattr(profile, "name", "?")
        self._pipeline_notify(
            f"[PROFILE] {profile_id} ({profile_name}) "
            f"→ резюме {resume.get('title')!r}",
            context.progress_callback,
        )

        # Build the per-profile "applications" service. VSA path (issue
        # #54) takes priority when ``application_prep_service_factory``
        # is provided; otherwise build the legacy ``ApplicationsService``
        # trio (VSA RelevanceHandler + CoverLetterService + TestHandler).
        applications: Any
        if context.application_prep_service_factory is not None:
            applications = context.application_prep_service_factory()
            if applications is not None:
                # Restore per-profile filter AI client (issue #54).
                if hasattr(applications, "prepare_filter_ai_client"):
                    applications.prepare_filter_ai_client(
                        profile,
                        resume,
                        context.vacancy_filter_ai_factory,
                        rate_limit=ai_rate_limit,
                    )
                if context.cover_letter_ai is not None and hasattr(
                    applications, "set_cover_letter_ai_client"
                ):
                    applications.set_cover_letter_ai_client(
                        context.cover_letter_ai
                    )
        else:
            # Issue #135: the legacy AI relevance shim is no longer wired
            # here. The VSA :class:`RelevanceHandler` is the single source
            # of truth for relevance filtering, so we reuse the slice's
            # shared handler instance and inject the per-profile filter AI
            # client via :func:`build_filter_ai_client`. The VSA handler is
            # duck-type compatible with the legacy
            # :class:`hh_applicant_tool.services.applications.ApplicationsService`
            # (``is_suitable_heavy`` / ``is_suitable_light`` returning
            # :class:`RelevanceResult` with ``suitable`` / ``score`` alias /
            # ``reason``).
            build_filter_ai_client(
                profile=profile,
                resume=resume,
                relevance_obj=self._relevance_handler,
                factory=context.vacancy_filter_ai_factory,
                rate_limit=ai_rate_limit,
            )
            from hh_applicant_tool.services import CoverLetterService

            cover_letter = CoverLetterService(
                context.api_client,
                context.cover_letter_ai,
                template=context.letter_template,
            )
            from job_bot.application_submit.handlers.test_handler import (
                TestHandler,
            )

            vacancy_tests = TestHandler(
                session=context.session,
                ai_client=context.test_ai or context.cover_letter_ai,
            )
            from hh_applicant_tool.services import ApplicationsService

            # Issue #135: the legacy ``ApplicationsService`` is typed
            # against the deprecated AI-relevance shim. The VSA
            # ``RelevanceHandler`` is duck-type compatible (same
            # ``is_suitable_heavy`` / ``is_suitable_light`` contract and
            # ``RelevanceResult`` shape) so we cast to ``Any`` for the
            # one interop call site.
            applications = ApplicationsService(
                context.storage,
                cast("Any", self._relevance_handler),
                cover_letter,
                vacancy_tests,
            )

        # Build per-profile search params and search service.
        search_params = self._pipeline_profile_search_params(profile)
        per_page = self._pipeline_profile_per_page(profile, per_page)
        total_pages = self._pipeline_profile_total_pages(profile, total_pages)

        if context.vacancy_search_service_factory is not None:
            search_service = context.vacancy_search_service_factory(
                per_page, total_pages
            )
        else:
            from hh_applicant_tool.services import VacancySearchService

            search_service = VacancySearchService(
                context.api_client,
                per_page=per_page,
                total_pages=total_pages,
            )

        from hh_applicant_tool.api.errors import ApiError, BadResponse

        try:
            vacancies = list(
                search_service.search(
                    search_params, resume_id=getattr(profile, "resume_id", None)
                )
            )
        except (requests.RequestException, ApiError, BadResponse) as ex:
            self._pipeline_notify(
                f"❌ Профиль {profile_id}: ошибка поиска — {ex}",
                context.progress_callback,
            )
            return

        self._pipeline_notify(
            f"[PROFILE] {profile_id}: найдено {len(vacancies)} вакансий",
            context.progress_callback,
        )

        for vacancy in vacancies:
            if self._pipeline_is_cancelled(context.cancellation):
                break
            stats.vacancies_seen += 1
            self._pipeline_process_vacancy(
                vacancy=vacancy,
                profile=profile,
                resume=resume,
                applications=applications,
                context=context,
                stats=stats,
                dry_run=dry_run,
                force_message=force_message,
            )

    def _pipeline_process_vacancy(
        self,
        *,
        vacancy: dict[str, Any],
        profile: Any,
        resume: dict[str, Any],
        applications: Any,
        context: PreparePipelineContext,
        stats: PreparePipelineStats,
        dry_run: bool,
        force_message: bool,
    ) -> None:
        """Prepare a single draft (or skip/reject)."""
        vacancy_id = vacancy.get("id")
        alt = vacancy.get("alternate_url") or vacancy_id

        # Skip policy.
        skip_reason = self._pipeline_skip_reason(
            vacancy, resume.get("id"), context.storage
        )
        if skip_reason:
            self._pipeline_notify(
                f"[SKIP] {skip_reason}: {alt}", context.progress_callback
            )
            stats.skipped += 1
            return

        if dry_run:
            self._pipeline_dry_run_print(vacancy, profile)
            stats.prepared += 1
            return

        full_vacancy = self._pipeline_safe_get_full_vacancy(
            vacancy_id, context.api_client
        )
        merged = self._pipeline_merge_vacancy(vacancy, full_vacancy)
        self._pipeline_save_vacancy_to_storage(merged, context.storage)
        self._pipeline_save_employer_to_storage(
            merged, context.api_client, context.storage
        )

        from hh_applicant_tool.api.errors import ApiError, BadResponse
        from hh_applicant_tool.storage.repositories.errors import (
            RepositoryError,
        )

        try:
            draft = applications.prepare_one(
                resume=resume,
                vacancy=merged,
                search_profile=profile,
                resume_analysis="",
                ai_filter_mode=getattr(profile, "ai_filter_mode", None),
                placeholders=self._pipeline_build_placeholders(resume),
                force_message=force_message,
                response_url=merged.get("response_url"),
            )
        except (
            RepositoryError,
            requests.RequestException,
            ApiError,
            BadResponse,
        ) as ex:
            self._pipeline_notify(
                f"[FAIL] {alt}: {ex}", context.progress_callback
            )
            stats.failed += 1
            return

        if draft is None:
            stats.skipped += 1
            return

        if getattr(draft, "status", None) == "rejected":
            self._pipeline_save_skipped_ai_rejected(
                merged, resume.get("id"), context
            )
            self._pipeline_notify(
                f"[REJECT] AI отклонил {alt} "
                f"(score={getattr(draft, 'relevance_score', None)})",
                context.progress_callback,
            )
            stats.rejected += 1
            return

        # Re-read the saved draft (UPSERT) to get the actual id.
        saved_draft = context.storage.application_drafts.get_by_resume_vacancy(
            str(resume.get("id") or ""), int(vacancy.get("id") or 0)
        )
        if saved_draft is None:
            saved_draft = draft
        stats.prepared += 1

        if (
            getattr(saved_draft, "has_test", False)
            and getattr(saved_draft, "test_status", None) == "generated"
        ):
            answers = list(
                context.storage.application_test_answers.find_by_draft(
                    saved_draft.id
                )
            )
            stats.test_answers += len(answers)
            self._pipeline_notify(
                f"[PREPARE] {alt} — draft={saved_draft.id}, "
                f"test_answers={len(answers)}",
                context.progress_callback,
            )
        else:
            extra = (
                " (test=manual_required)"
                if getattr(saved_draft, "has_test", False)
                else ""
            )
            self._pipeline_notify(
                f"[PREPARE] {alt} — draft={saved_draft.id}{extra}",
                context.progress_callback,
            )

    @staticmethod
    def _pipeline_is_cancelled(cancellation: Any) -> bool:
        """Check cancellation via ``CancellationToken`` port (issue #35)."""
        if cancellation is not None:
            return bool(getattr(cancellation, "is_cancelled", False))
        return False

    @staticmethod
    def _pipeline_notify(
        message: str, progress_callback: Callable[[str], None] | None
    ) -> None:
        """Print + invoke progress callback (matches legacy ``_notify``)."""
        print(message)
        if progress_callback is not None:
            try:
                progress_callback(message)
            except Exception as ex:  # noqa: BLE001
                logger.warning("progress_callback error: %s", ex)

    @staticmethod
    def _pipeline_dry_run_print(vacancy: dict[str, Any], profile: Any) -> None:
        """Print what would have been prepared in dry-run mode."""
        vid = vacancy.get("id")
        alt = vacancy.get("alternate_url") or vid
        has_test = bool(vacancy.get("has_test"))
        profile_id = getattr(profile, "id", "?")
        print(
            f"[DRY-RUN] Профиль {profile_id}: "
            f"подготовили бы черновик для "
            f"{alt} (id={vid}, has_test={has_test})"
        )

    @staticmethod
    def _pipeline_skip_reason(
        vacancy: dict[str, Any],
        resume_id: str | None,
        storage: Any,
    ) -> str | None:
        """Return a skip reason string or ``None``."""
        if vacancy.get("relations"):
            return "already_responded"
        if vacancy.get("archived"):
            return "archived"
        if ApplicationPrepSlice._pipeline_is_vacancy_already_skipped(
            vacancy, resume_id, storage
        ):
            return "previously_skipped"
        return None

    @staticmethod
    def _pipeline_is_vacancy_already_skipped(
        vacancy: dict[str, Any], resume_id: str | None, storage: Any
    ) -> bool:
        """Return True if vacancy has been previously skipped."""
        vacancy_id = vacancy.get("id")
        if vacancy_id is None:
            return False
        from hh_applicant_tool.storage.repositories.errors import (
            RepositoryError,
        )

        try:
            if resume_id and any(
                storage.skipped_vacancies.find(
                    resume_id=resume_id, vacancy_id=vacancy_id
                )
            ):
                return True
            return any(
                storage.skipped_vacancies.find(
                    resume_id="", vacancy_id=vacancy_id
                )
            )
        except RepositoryError:
            return False

    @staticmethod
    def _pipeline_save_skipped_ai_rejected(
        vacancy: dict[str, Any],
        resume_id: str | None,
        context: PreparePipelineContext,
    ) -> None:
        """Persist AI-rejected vacancy to skipped_vacancies."""
        employer = vacancy.get("employer") or {}
        created_at = (
            context.clock.now() if context.clock is not None else datetime.now()
        )
        from hh_applicant_tool.storage.repositories.errors import (
            RepositoryError,
        )

        try:
            context.storage.skipped_vacancies.save(
                {
                    "resume_id": resume_id or "",
                    "vacancy_id": vacancy.get("id"),
                    "reason": "ai_rejected",
                    "alternate_url": vacancy.get("alternate_url"),
                    "name": vacancy.get("name"),
                    "employer_name": employer.get("name"),
                    "created_at": created_at,
                }
            )
        except RepositoryError as ex:
            logger.warning("Не удалось сохранить skipped_vacancy: %s", ex)

    @staticmethod
    def _pipeline_save_vacancy_to_storage(
        vacancy: dict[str, Any], storage: Any
    ) -> None:
        """Persist vacancy (+ contacts) to storage."""
        from hh_applicant_tool.storage.repositories.errors import (
            RepositoryError,
        )

        try:
            storage.vacancies.save(vacancy)
        except RepositoryError as ex:
            logger.debug(ex)
        if vacancy.get("contacts"):
            try:
                storage.vacancy_contacts.save(vacancy)
            except RepositoryError as ex:
                logger.exception(ex)

    @staticmethod
    def _pipeline_save_employer_to_storage(
        vacancy: dict[str, Any], api_client: Any, storage: Any
    ) -> None:
        """Persist employer profile to storage."""
        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")
        if not employer_id:
            return
        from hh_applicant_tool.api.errors import ApiError, BadResponse

        try:
            profile = api_client.get(f"/employers/{employer_id}")
        except (requests.RequestException, ApiError, BadResponse) as ex:
            logger.debug(
                "Не удалось получить профиль работодателя: %s",
                ex,
            )
            return
        from hh_applicant_tool.storage.repositories.errors import (
            RepositoryError,
        )

        try:
            storage.employers.save(profile)
        except RepositoryError as ex:
            logger.exception(ex)

    @staticmethod
    def _pipeline_safe_get_full_vacancy(
        vacancy_id: Any, api_client: Any
    ) -> dict[str, Any] | None:
        """Fetch full vacancy data; return None on error."""
        if vacancy_id is None:
            return None
        from hh_applicant_tool.api.errors import ApiError, BadResponse

        try:
            return cast(
                "dict[str, Any] | None",
                api_client.get(f"/vacancies/{vacancy_id}"),
            )
        except (requests.RequestException, ApiError, BadResponse) as ex:
            logger.debug(
                "Не удалось получить полную вакансию %s: %s",
                vacancy_id,
                ex,
            )
            return None

    @staticmethod
    def _pipeline_merge_vacancy(
        search_vacancy: dict[str, Any],
        full_vacancy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Merge search + full vacancy (priority to ``full_vacancy``)."""
        if not full_vacancy:
            return search_vacancy
        merged = dict(full_vacancy)
        for key in (
            "relations",
            "has_test",
            "alternate_url",
            "response_url",
            "response_letter_required",
        ):
            if key not in merged and key in search_vacancy:
                merged[key] = search_vacancy[key]
        return merged

    @staticmethod
    def _pipeline_profile_search_params(profile: Any) -> dict[str, Any]:
        """Extract search params from profile (drops per_page/total_pages)."""
        params: dict[str, Any] = dict(
            getattr(profile, "search_params", None) or {}
        )
        params.pop("per_page", None)
        params.pop("total_pages", None)
        return params

    @staticmethod
    def _pipeline_profile_per_page(profile: Any, default: int) -> int:
        """Extract per_page from profile.search_params, fall back to default."""
        params = getattr(profile, "search_params", None) or {}
        value = params.get("per_page")
        return int(value) if value else default

    @staticmethod
    def _pipeline_profile_total_pages(profile: Any, default: int) -> int:
        """Extract total_pages from profile.search_params, fall back to default."""
        params = getattr(profile, "search_params", None) or {}
        value = params.get("total_pages")
        return int(value) if value else default

    @staticmethod
    def _pipeline_build_placeholders(resume: dict[str, Any]) -> dict[str, Any]:
        """Build placeholder dict for cover letter template."""
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

    @property
    def relevance(self) -> RelevancePort:
        """Get relevance analysis port."""
        return self._relevance_handler

    @property
    def relevance_storage(self) -> RelevanceStoragePort:
        """Get relevance storage port."""
        return self._relevance_handler

    @property
    def cover_letters(self) -> CoverLetterPort:
        """Get cover letter port."""
        return self._cover_letter_handler

    @property
    def applications(self) -> ApplicationPort:
        """Get application draft port."""
        return self._application_handler

    @property
    def database(self) -> Database:
        """Get database instance."""
        return self._database

    @property
    def api_client(self) -> HHApiClient:
        """Get API client instance."""
        return self._api_client


def create_application_prep_slice(
    settings: Settings | None = None,
    database: Database | None = None,
    api_client: HHApiClient | None = None,
    api_config: HHApiConfig | None = None,
    ai_client: "AIClient | None" = None,
    ai_config: "AIConfig | None" = None,
    *,
    relevance_rules: dict[str, Any] | None = None,
    ai_failure_mode: str = "permissive",
    cover_letter_template: str | None = None,
    vacancy_port: "VacancyPort | None" = None,
) -> ApplicationPrepSlice:
    """Factory function to create an ApplicationPrepSlice.

    Args:
        settings: Application settings (optional, will create default if not provided)
        database: Database instance (optional, will create from settings if not provided)
        api_client: HH API client (optional, will create if not provided)
        api_config: HH API config (optional, will use from settings if not provided)
        ai_client: AI client (optional, will create from ai_config if not provided)
        ai_config: AI config (optional, will use from settings if not provided)
        relevance_rules: relevance filtering rules (must_have, nice_to_have, etc.)
        ai_failure_mode: "permissive" | "strict" | "raise" - what to do on AI failure
        cover_letter_template: custom cover letter template (otherwise default)
        vacancy_port: VacancyPort for fetching full vacancy data

    Returns:
        Configured ApplicationPrepSlice instance
    """
    if settings is None:
        from job_bot.shared.config.settings import load_settings

        settings = load_settings()

    if database is None:
        database = create_database(settings.database.path)

    if api_config is None:
        api_config = HHApiConfig(
            base_url=settings.hh_api.base_url,
            user_agent=settings.hh_api.user_agent,
            timeout=settings.hh_api.timeout,
        )

    if api_client is None:
        api_client = HHApiClient(config=api_config)

    if ai_client is None and ai_config is not None:
        from job_bot.shared.ai.client import AIClient

        ai_client = AIClient(config=ai_config)

    return ApplicationPrepSlice(
        database=database,
        api_client=api_client,
        ai_client=ai_client,
        relevance_rules=relevance_rules,
        ai_failure_mode=ai_failure_mode,
        cover_letter_template=cover_letter_template,
        vacancy_port=vacancy_port,
    )
