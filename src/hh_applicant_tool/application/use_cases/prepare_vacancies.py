"""Use case: ``prepare-vacancies`` (issue #5).

Подготовка черновиков откликов (draft + cover letter + test answers) для
search-профилей из БД — **БЕЗ отправки откликов** на hh.ru. Используется
CLI-командой ``hh-applicant-tool prepare-vacancies`` и будет переиспользоваться
Telegram-ботом (issue #7-9) и apply-worker'ом (issue #10).

Use case зависит только от инфраструктурных клиентов (``api_client``,
``session``, ``storage``, AI-фабрики) — не знает про ``HHApplicantTool`` или
argparse. Это позволяет запускать его из CLI, UI или Telegram-бота.

Phase 2 (Clean Architecture): порты ``CancellationToken`` и ``Clock``
принимаются опционально через конструктор (issue #35).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Iterable

import requests

from job_bot.application_prep.slice import (
    ApplicationPrepSlice,
    PreparePipelineContext,
    PreparePipelineStats,
)
from job_bot.application_prep.utils import build_filter_ai_client

from ...api.errors import BadResponse
from ...api.errors import ApiError
from ...services import (
    DEFAULT_LETTER_TEMPLATE,
    ApplicationsService,
    CoverLetterService,
    RelevanceService,
    VacancySearchService,
)
from ...storage.models.search_profile import SearchProfileModel
from ...storage.repositories.errors import RepositoryError
from ..dto import PrepareVacanciesCommand, PrepareVacanciesResult

if TYPE_CHECKING:
    from ..ports import CancellationToken, Clock

logger = logging.getLogger(__package__)


ProgressCallback = Callable[[str], None]


class PrepareVacanciesUseCase:
    """Оркестратор подготовки черновиков откликов.

    Алгоритм:
        1. Загрузить search-профили (все включённые или один по ``--search-profile``).
        2. Загрузить опубликованные резюме пользователя (и сохранить в storage).
        3. Для каждого профиля:
            - сконструировать ``RelevanceService`` (AI-фильтр),
              ``CoverLetterService`` (AI-генерация письма),
              ``VacancyTestsService`` (AI-генерация ответов на тесты),
              ``ApplicationsService`` (оркестратор);
            - выполнить поиск вакансий через ``VacancySearchService``;
            - для каждой вакансии: skip-policy → ``ApplicationsService.prepare_one``
              → сохранить в ``skipped_vacancies`` при ``ai_rejected``.
        4. Вернуть :class:`PrepareVacanciesResult` со статистикой.

    Команда **никогда** не вызывает ``api_client.post`` и методы отправки
    (``VacancyTestsService.submit_apply`` и т.п.).

    Attributes:
        api_client: ``api.client.ApiClient`` — HTTP-клиент HH API.
        session: ``requests.Session`` — низкоуровневая сессия
            (используется :class:`VacancyTestsService` для парсинга
            тестов с ``/applicant/vacancy_response``).
        storage: ``storage.StorageFacade`` — фасад локальной БД.
        cover_letter_ai: ``ai.ChatOpenAI`` с system_prompt для генерации
            писем или ``None`` (тогда письмо по шаблону).
        vacancy_filter_ai_factory: ``Callable[[str], ChatOpenAI]`` —
            фабрика AI-клиента фильтра вакансий (получает system_prompt,
            возвращает AI). Если фильтр не нужен — ``None``.
        test_ai: ``ai.ChatOpenAI`` для генерации ответов на тесты.
            Если ``None`` — переиспользуется ``cover_letter_ai``.
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
        # Vacancy search service (optional, for VSA wiring)
        vacancy_search_service_factory: Any = None,
        # Application Prep service factory (VSA wiring, issue #54)
        application_prep_service_factory: Any = None,
        # Application Prep slice (VSA bridge, issue #90). When provided,
        # the use case delegates the per-profile → per-vacancy pipeline
        # to ``ApplicationPrepSlice.run_prepare_pipeline()`` instead of
        # running its own ``_process_profile`` / ``_process_vacancy``
        # loop. The legacy path (no slice) is preserved for backward
        # compat with tests that don't wire the slice.
        application_prep_slice: ApplicationPrepSlice | None = None,
    ) -> None:
        self.api_client = api_client
        self.session = session
        self.storage = storage
        self.cover_letter_ai = cover_letter_ai
        self.vacancy_filter_ai_factory = vacancy_filter_ai_factory
        self.test_ai = test_ai
        self.letter_template = letter_template or DEFAULT_LETTER_TEMPLATE

        # Phase 2 ports
        self._cancellation = cancellation
        self._clock = clock

        # Внедрённый сервис поиска вакансов (VSA wiring)
        self._injected_vacancy_search_service_factory = (
            vacancy_search_service_factory
        )

        # Внедрённый сервис подготовки черновиков (VSA wiring, issue #54)
        # Когда задан — use case делегирует ``ApplicationsService.prepare_one``
        # этому адаптеру (который внутри использует ``ApplicationPrepSlice``).
        self._injected_application_prep_service_factory = (
            application_prep_service_factory
        )
        # VSA bridge (issue #90): optional ``ApplicationPrepSlice``.
        # When set, ``execute()`` delegates the per-profile pipeline to
        # ``ApplicationPrepSlice.run_prepare_pipeline()``. The legacy
        # ``_process_profile`` / ``_process_vacancy`` path is kept as
        # a fallback for tests that don't wire the slice.
        self._application_prep_slice = application_prep_slice

        # Состояние, заполняемое в execute().
        self.command: PrepareVacanciesCommand | None = None
        self.cancel_event: threading.Event | None = None
        self.progress_callback: ProgressCallback | None = None

    # ─── Публичный API ───────────────────────────────────────────

    def execute(
        self,
        command: PrepareVacanciesCommand,
        *,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> PrepareVacanciesResult:
        """Запускает подготовку черновиков.

        Args:
            command: входные параметры (``PrepareVacanciesCommand``).
            cancel_event: опциональное событие отмены.
            progress_callback: опциональный колбэк прогресса.
                Вызывается с теми же строками, что выводятся в stdout,
                — для интеграции с UI / Telegram.

        Returns:
            :class:`PrepareVacanciesResult` со статистикой.
        """
        self.command = command
        self.cancel_event = cancel_event
        self.progress_callback = progress_callback

        result = PrepareVacanciesResult()

        profiles = list(self._load_profiles(command.search_profile))
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

        resumes = self._fetch_published_resumes()
        if not resumes:
            logger.warning("У вас нет опубликованных резюме")
            self._notify("⚠️ Нет опубликованных резюме — нечего готовить")
            return result

        resumes_by_id: dict[str, dict[str, Any]] = {
            str(r["id"]): r for r in resumes
        }

        # VSA bridge (issue #90): when a slice is injected, delegate the
        # per-profile → per-vacancy pipeline to it. The legacy
        # ``_process_profile`` / ``_process_vacancy`` path below is kept
        # as a fallback for tests that don't wire the slice.
        if self._application_prep_slice is not None:
            return self._execute_via_slice(
                profiles=profiles,
                resumes_by_id=resumes_by_id,
                command=command,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )

        for profile in profiles:
            if self._is_cancelled():
                break
            result.profiles_processed += 1
            self._process_profile(profile, resumes_by_id, command, result)

        return result

    def _execute_via_slice(
        self,
        *,
        profiles: list[SearchProfileModel],
        resumes_by_id: dict[str, dict[str, Any]],
        command: PrepareVacanciesCommand,
        cancel_event: threading.Event | None,
        progress_callback: ProgressCallback | None,
    ) -> PrepareVacanciesResult:
        """Delegate the per-profile pipeline to the VSA slice (issue #90).

        The legacy :meth:`execute` calls this method when
        ``application_prep_slice`` was injected at construction time.
        The slice does the per-profile → per-vacancy pipeline
        (search, AI filter, cover letter, draft save) and returns a
        :class:`PreparePipelineStats` which we convert to the legacy
        :class:`PrepareVacanciesResult`.

        Note: the slice method owns cancellation via the
        ``PreparePipelineContext.cancellation`` port. We pass our
        ``self._cancellation`` (Clean Architecture port) when set;
        otherwise we ignore ``cancel_event`` (the legacy
        ``threading.Event``-based check stays in the legacy path).
        """
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
        stats: PreparePipelineStats = self._application_prep_slice.run_prepare_pipeline(
            profiles=profiles,
            resumes_by_id=resumes_by_id,
            context=context,
            dry_run=command.dry_run,
            per_page=command.per_page,
            total_pages=command.total_pages,
            force_message=command.force_message,
            ai_rate_limit=command.ai_rate_limit,
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

    # ─── Загрузка профилей и резюме ──────────────────────────────

    def _load_profiles(
        self, profile_id: str | None
    ) -> Iterable[SearchProfileModel]:
        """Возвращает профиль(и) для обработки.

        - ``profile_id`` задан: ``storage.search_profiles.get(profile_id)``;
          если ``enabled=False`` — пропускаем с предупреждением
          (явный выбор пользователя уважается, но мы обязаны сообщить).
        - ``profile_id is None``: ``storage.search_profiles.find_enabled()``.
        """
        if profile_id:
            profile = self.storage.search_profiles.get(profile_id)
            if profile is None:
                logger.warning("Search profile %r не найден", profile_id)
                return []
            if not profile.enabled:
                logger.warning(
                    "Search profile %r выключен (enabled=False) — "
                    "обрабатываю по явному запросу",
                    profile_id,
                )
                self._notify(
                    f"⚠️ Профиль {profile_id} выключен — обрабатываю "
                    "по явному запросу"
                )
            return [profile]
        return list(self.storage.search_profiles.find_enabled())

    def _fetch_published_resumes(self) -> list[dict[str, Any]]:
        """Загружает ``/resumes/mine``, сохраняет в storage, фильтрует
        по статусу ``published``. При ``dry_run`` не пишет в БД.
        """
        resumes: list[dict[str, Any]] = (
            self.api_client.get("/resumes/mine").get("items") or []
        )
        if not self.command.dry_run:
            try:
                self.storage.resumes.save_batch(resumes)
            except RepositoryError as ex:
                logger.debug(ex)
        return [
            r
            for r in resumes
            if (r.get("status") or {}).get("id") == "published"
        ]

    # ─── Обработка одного профиля ────────────────────────────────

    def _process_profile(
        self,
        profile: SearchProfileModel,
        resumes_by_id: dict[str, dict[str, Any]],
        command: PrepareVacanciesCommand,
        result: PrepareVacanciesResult,
    ) -> None:
        """Готовит черновики для одного search-профиля."""
        resume = resumes_by_id.get(profile.resume_id)
        if resume is None:
            logger.warning(
                "Резюме %s не найдено среди опубликованных — "
                "пропускаю профиль %s",
                profile.resume_id,
                profile.id,
            )
            self._notify(
                f"⚠️ Профиль {profile.id}: резюме {profile.resume_id} "
                "не опубликовано — пропускаю"
            )
            return

        self._notify(
            f"[PROFILE] {profile.id} ({profile.name}) "
            f"→ резюме {resume.get('title')!r}"
        )

        # VSA wiring (issue #54): if a new-style application prep service
        # is injected, use it instead of building the legacy
        # ``ApplicationsService`` + ``RelevanceService`` + ``CoverLetterService``
        # trio. The adapter still writes to ``self.storage`` so downstream
        # code keeps working.
        if self._injected_application_prep_service_factory is not None:
            applications = self._injected_application_prep_service_factory()
            # Build the per-profile filter AI client (with system prompt
            # baked in via ``vacancy_filter_ai_factory``) and inject it
            # into the slice's ``RelevanceHandler`` via the new setter.
            # This restores the per-profile filter behaviour that the old
            # ``RelevanceService.ai_client`` assignment used to provide.
            rate_limit = (
                self.command.ai_rate_limit if self.command is not None else None
            )
            applications.prepare_filter_ai_client(
                profile,
                resume,
                self.vacancy_filter_ai_factory,
                rate_limit=rate_limit,
            )
            # Also inject the cover-letter AI client if ``--use-ai`` was
            # passed (the slice may have been constructed without one).
            if self.cover_letter_ai is not None:
                applications.set_cover_letter_ai_client(self.cover_letter_ai)
        else:
            relevance = self._build_relevance_service(profile, resume)
            cover_letter = CoverLetterService(
                self.api_client,
                self.cover_letter_ai,
                template=self.letter_template,
            )
            # Use the VSA TestHandler for test-answer generation.
            # The handler is owned by the ApplicationSubmit slice (issue #77).
            from job_bot.application_submit.handlers.test_handler import (
                TestHandler,
            )

            vacancy_tests = TestHandler(
                session=self.session,
                ai_client=self.test_ai or self.cover_letter_ai,
            )
            applications = ApplicationsService(
                self.storage, relevance, cover_letter, vacancy_tests
            )

        # ``search_params`` хранится в профиле; ``per_page``/``total_pages``
        # из команды имеют приоритет. Ключи ``per_page``/``total_pages``
        # из профиля выкидываем — иначе VacancySearchService.search всё
        # равно их перезапишет.
        search_params = self._profile_search_params(profile)
        per_page = self._profile_per_page(profile, command.per_page)
        total_pages = self._profile_total_pages(profile, command.total_pages)

        # Use injected vacancy search service factory (VSA wiring) or fall back to old service
        if self._injected_vacancy_search_service_factory is not None:
            search_service = self._injected_vacancy_search_service_factory(
                per_page, total_pages
            )
        else:
            search_service = VacancySearchService(
                self.api_client,
                per_page=per_page,
                total_pages=total_pages,
            )

        try:
            vacancies = list(
                search_service.search(
                    search_params, resume_id=profile.resume_id
                )
            )
        except (requests.RequestException, ApiError, BadResponse) as ex:
            logger.exception(
                "Ошибка при поиске вакансий для профиля %s: %s",
                profile.id,
                ex,
            )
            self._notify(f"❌ Профиль {profile.id}: ошибка поиска — {ex}")
            return

        self._notify(
            f"[PROFILE] {profile.id}: найдено {len(vacancies)} вакансий"
        )

        for vacancy in vacancies:
            if self._is_cancelled():
                break
            result.vacancies_seen += 1
            self._process_vacancy(
                vacancy=vacancy,
                profile=profile,
                resume=resume,
                applications=applications,
                command=command,
                result=result,
            )

    def _process_vacancy(
        self,
        *,
        vacancy: dict[str, Any],
        profile: SearchProfileModel,
        resume: dict[str, Any],
        applications: ApplicationsService,
        command: PrepareVacanciesCommand,
        result: PrepareVacanciesResult,
    ) -> None:
        """Подготавливает один черновик (или skip/reject)."""
        vacancy_id = vacancy.get("id")
        alt = vacancy.get("alternate_url") or vacancy_id

        # Skip policy
        skip_reason = self._skip_reason(vacancy, resume.get("id"))
        if skip_reason:
            logger.debug("Пропускаю %s: %s", alt, skip_reason)
            self._notify(f"[SKIP] {skip_reason}: {alt}")
            result.skipped += 1
            return

        if command.dry_run:
            self._dry_run_print(vacancy, profile)
            result.prepared += 1
            return

        # Полная вакансия — нам нужны ``description``, ``key_skills``,
        # ``response_url`` и пр. для AI-фильтра, письма и тестов.
        full_vacancy = self._safe_get_full_vacancy(vacancy_id)

        merged = self._merge_vacancy(vacancy, full_vacancy)

        # Сохраняем vacancy + contacts + employer.
        self._save_vacancy_to_storage(merged)
        self._save_employer_to_storage(merged)

        # Запускаем основной pipeline (AI-фильтр, письмо, тесты).
        try:
            draft = applications.prepare_one(
                resume=resume,
                vacancy=merged,
                search_profile=profile,
                resume_analysis="",  # AI-фильтр analyze_* вызовет RelevanceService сам
                ai_filter_mode=profile.ai_filter_mode,
                placeholders=self._build_placeholders(resume),
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
            self._notify(f"[FAIL] {alt}: {ex}")
            result.failed += 1
            return

        if draft is None:
            result.skipped += 1
            return

        if draft.status == "rejected":
            self._save_skipped_ai_rejected(merged, resume.get("id"))
            self._notify(
                f"[REJECT] AI отклонил {alt} (score={draft.relevance_score})"
            )
            result.rejected += 1
            return

        # status == "prepared"
        # draft.id известен только после UPSERT — перечитываем запись
        # из БД, чтобы иметь актуальный id для счётчика тест-ответов.
        saved_draft = self.storage.application_drafts.get_by_resume_vacancy(
            str(resume.get("id") or ""), int(vacancy.get("id") or 0)
        )
        if saved_draft is None:
            logger.warning(
                "Не удалось перечитать черновик (%s, %s) после save",
                resume.get("id"),
                vacancy.get("id"),
            )
            saved_draft = draft
        result.prepared += 1

        if saved_draft.has_test and saved_draft.test_status == "generated":
            answers = list(
                self.storage.application_test_answers.find_by_draft(
                    saved_draft.id
                )
            )
            result.test_answers += len(answers)
            self._notify(
                f"[PREPARE] {alt} — draft={saved_draft.id}, "
                f"test_answers={len(answers)}"
            )
        else:
            self._notify(
                f"[PREPARE] {alt} — draft={saved_draft.id}"
                + (" (test=manual_required)" if saved_draft.has_test else "")
            )

    # ─── Skip policy ─────────────────────────────────────────────

    def _skip_reason(
        self, vacancy: dict[str, Any], resume_id: str | None
    ) -> str | None:
        """Возвращает строку-причину пропуска или ``None``."""
        if vacancy.get("relations"):
            return "already_responded"
        if vacancy.get("archived"):
            return "archived"
        if self._is_vacancy_already_skipped(vacancy, resume_id):
            return "previously_skipped"
        return None

    def _is_vacancy_already_skipped(
        self, vacancy: dict[str, Any], resume_id: str | None
    ) -> bool:
        vacancy_id = vacancy.get("id")
        if vacancy_id is None:
            return False
        try:
            if resume_id and any(
                self.storage.skipped_vacancies.find(
                    resume_id=resume_id, vacancy_id=vacancy_id
                )
            ):
                return True
            return any(
                self.storage.skipped_vacancies.find(
                    resume_id="", vacancy_id=vacancy_id
                )
            )
        except RepositoryError:
            return False

    def _save_skipped_ai_rejected(
        self, vacancy: dict[str, Any], resume_id: str | None
    ) -> None:
        employer = vacancy.get("employer") or {}
        created_at = self._clock.now() if self._clock else datetime.now()
        try:
            self.storage.skipped_vacancies.save(
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

    # ─── Сохранение vacancy/employer ─────────────────────────────

    def _save_vacancy_to_storage(self, vacancy: dict[str, Any]) -> None:
        try:
            self.storage.vacancies.save(vacancy)
        except RepositoryError as ex:
            logger.debug(ex)
        if vacancy.get("contacts"):
            try:
                self.storage.vacancy_contacts.save(vacancy)
            except RepositoryError as ex:
                logger.exception(ex)

    def _save_employer_to_storage(self, vacancy: dict[str, Any]) -> None:
        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")
        if not employer_id:
            return
        try:
            profile = self.api_client.get(f"/employers/{employer_id}")
        except (requests.RequestException, ApiError, BadResponse) as ex:
            logger.debug("Не удалось получить профиль работодателя: %s", ex)
            return
        try:
            self.storage.employers.save(profile)
        except RepositoryError as ex:
            logger.exception(ex)

    def _safe_get_full_vacancy(self, vacancy_id: Any) -> dict[str, Any] | None:
        if vacancy_id is None:
            return None
        try:
            return self.api_client.get(f"/vacancies/{vacancy_id}")
        except (requests.RequestException, ApiError, BadResponse) as ex:
            logger.debug(
                "Не удалось получить полную вакансию %s: %s",
                vacancy_id,
                ex,
            )
            return None

    @staticmethod
    def _merge_vacancy(
        search_vacancy: dict[str, Any],
        full_vacancy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Мержит search-результат и full-вакансию.

        Приоритет — у ``full_vacancy`` (там есть ``description``/``key_skills``/
        ``response_url``), но ``relations``/``has_test``/``alternate_url``/
        ``response_url`` берём из search-результата, если их там нет в full.
        """
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

    # ─── AI-фильтр (per-profile) ─────────────────────────────────

    def _build_relevance_service(
        self,
        profile: SearchProfileModel,
        resume: dict[str, Any],
    ) -> RelevanceService:
        """Создаёт :class:`RelevanceService` для профиля.

        Анализ резюме и system_prompt AI-фильтра считаются здесь (один раз
        на профиль). Сам AI-клиент устанавливается в
        :class:`ApplicationsService` через ``relevance.ai_client`` уже после
        того, как factory вернёт инстанс.

        Делегирует построение AI-клиента общему хелперу
        :func:`job_bot.application_prep.utils.build_filter_ai_client` —
        единая логика для legacy- и VSA-пути (issue #54).
        """
        relevance = RelevanceService(
            self.api_client,
            ai_client=None,
            relevance_rules=profile.relevance_rules,
        )
        build_filter_ai_client(
            profile=profile,
            resume=resume,
            relevance_obj=relevance,
            factory=self.vacancy_filter_ai_factory,
            rate_limit=(
                self.command.ai_rate_limit if self.command is not None else None
            ),
        )
        return relevance

    # ─── Профильные search-параметры ─────────────────────────────

    @staticmethod
    def _profile_search_params(
        profile: SearchProfileModel,
    ) -> dict[str, Any]:
        params: dict[str, Any] = dict(profile.search_params or {})
        # ``per_page``/``total_pages`` обслуживаются VacancySearchService.
        params.pop("per_page", None)
        params.pop("total_pages", None)
        return params

    @staticmethod
    def _profile_per_page(profile: SearchProfileModel, default: int) -> int:
        params = profile.search_params or {}
        value = params.get("per_page")
        return int(value) if value else default

    @staticmethod
    def _profile_total_pages(profile: SearchProfileModel, default: int) -> int:
        params = profile.search_params or {}
        value = params.get("total_pages")
        return int(value) if value else default

    # ─── Placeholders / progress / cancel ────────────────────────

    @staticmethod
    def _build_placeholders(
        resume: dict[str, Any],
    ) -> dict[str, Any]:
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

    def _is_cancelled(self) -> bool:
        """Проверяет отмену через ``CancellationToken`` (issue #35)
        или ``threading.Event``."""
        if self._cancellation is not None:
            return self._cancellation.is_cancelled
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _notify(self, *args: Any) -> None:
        message = " ".join(str(a) for a in args)
        print(message)
        if self.progress_callback is not None:
            try:
                self.progress_callback(message)
            except Exception as ex:  # noqa: BLE001
                logger.warning("progress_callback error: %s", ex)

    @staticmethod
    def _dry_run_print(
        vacancy: dict[str, Any], profile: SearchProfileModel
    ) -> None:
        vid = vacancy.get("id")
        alt = vacancy.get("alternate_url") or vid
        has_test = bool(vacancy.get("has_test"))
        print(
            f"[DRY-RUN] Профиль {profile.id}: подготовили бы черновик для "
            f"{alt} (id={vid}, has_test={has_test})"
        )
