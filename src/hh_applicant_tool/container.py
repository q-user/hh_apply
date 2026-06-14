"""Composition root.

:class:`AppContainer` — единая точка сборки зависимостей (DI-wiring) для
всех клиентов приложения (CLI, UI, Telegram-бот, worker). Каждый клиент
получает готовый use case, не собирая его вручную и не зная о
внутренних сервисах (``HHApplicantTool`` предоставляет инфраструктурные
клиенты; use case получает их через конструктор — явный DI).

Использование::

    container = AppContainer(tool)
    use_case = container.apply_to_vacancies_use_case(
        system_prompt=...,
        use_ai=...,
        send_email=...,
    )
    result = use_case.execute(command, cancel_event=...)

Внутри ``AppContainer`` инкапсулирует те же вызовы ``tool.*``, что
раньше дублировались в ``operations/apply_vacancies.py:run()`` и в
``ui/api.py:apply_vacancies()`` (issue #16).
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from job_bot.application_prep.utils import build_filter_ai_client

from .application import ApplyToVacanciesUseCase, PrepareVacanciesUseCase
from .constants import CONFIG_FILENAME

if TYPE_CHECKING:
    from .main import HHApplicantTool

logger = logging.getLogger(__name__)


class AppContainer:
    """Composition root. Централизует DI-wiring для use case'ов.

    Attributes:
        tool: инфраструктурный фасад ``HHApplicantTool`` — от него
            берутся API-клиент, HTTP-сессия, storage, AI/smtp-клиенты,
            конфиг и токены.
    """

    def __init__(self, tool: "HHApplicantTool") -> None:
        self._tool = tool
        # Phase 2: lazy port singletons
        self._site_parser = None
        self._email_sender = None
        self._test_logger = None
        # Vacancy Search slice (VSA)
        self._vacancy_search_slice = None
        # Config Auth slice (VSA)
        self._config_auth_slice = None
        self._config_adapter = None
        # Application Prep slice (VSA) — issue #54
        self._application_prep_slice = None
        self._application_prep_adapter = None
        # Application Submit slice (VSA) — issue #55
        self._application_submit_slice = None
        self._application_submit_adapter = None
        # MAX Bot slice (VSA) — issue #58
        self._max_bot_slice = None
        # Telegram Bot slice (VSA) — issue #56
        self._telegram_bot_slice = None
        self._telegram_bot_adapter = None
        # Channel Monitoring slice (VSA) — issue #57
        self._channel_monitor_slice = None

    # ─── Phase 2 port factories (lazy) ───────────────────────────

    def _get_site_parser(self):
        if self._site_parser is None:
            from .infrastructure.http import RequestsSiteParser

            self._site_parser = RequestsSiteParser(
                session=self._tool.session,
            )
        return self._site_parser

    def _get_email_sender(self):
        if self._email_sender is None:
            from .infrastructure.email import SMTPEmailSenderFromConfig

            cfg = (self._tool.config or {}).get("smtp", {})
            if cfg:
                self._email_sender = SMTPEmailSenderFromConfig.create(cfg)
            else:
                self._email_sender = False  # sentinel for "not configured"
        return self._email_sender if self._email_sender is not False else None

    def _get_test_logger(self):
        if self._test_logger is None:
            from .infrastructure.test_logger import FileTestVacancyLogger

            self._test_logger = FileTestVacancyLogger()
        return self._test_logger

    # ─── Vacancy Search Slice (VSA) ────────────────────────────────

    def _get_vacancy_search_slice(self):
        """Get or create the VacancySearchSlice instance."""
        if self._vacancy_search_slice is None:
            from job_bot.shared.config.settings import Settings
            from job_bot.shared.storage.database import create_database
            from job_bot.vacancy_search.slice import create_vacancy_search_slice

            tool = self._tool
            # Build shared kernel Settings from tool's config
            config = tool.config
            settings = Settings()
            settings.database.path = tool.db_path

            hh_config = config.get("hh_api", {})
            settings.hh_api.base_url = hh_config.get(
                "base_url", "https://api.hh.ru"
            )
            settings.hh_api.user_agent = hh_config.get(
                "user_agent", "job_bot/0.1.0"
            )
            settings.hh_api.timeout = hh_config.get("timeout", 30)
            settings.hh_api.client_id = config.get("client_id")
            settings.hh_api.client_secret = config.get("client_secret")

            # Create slice with shared kernel components
            self._vacancy_search_slice = create_vacancy_search_slice(
                settings=settings,
                database=create_database(settings.database.path),
            )
        return self._vacancy_search_slice

    def create_vacancy_search_adapter(self, per_page: int, total_pages: int):
        """Create an adapter that provides the old VacancySearchService interface
        but delegates to the new VacancySearchSlice.
        """
        return _VacancySearchAdapter(
            slice=self._get_vacancy_search_slice(),
            tool=self._tool,
            per_page=per_page,
            total_pages=total_pages,
        )

    # ─── Config Auth Slice (VSA) ───────────────────────────────────

    def _get_config_auth_slice(self) -> Any:
        """Get or create the ConfigAuthSlice instance (issue #59)."""
        if self._config_auth_slice is None:
            from job_bot.config_auth.slice import create_config_auth_slice
            from job_bot.shared.config.settings import Settings
            from job_bot.shared.storage.database import create_database

            tool = self._tool
            # Build shared kernel Settings from tool's config. We
            # avoid touching ``tool.config`` here (it would recurse
            # back into the container on the very first access) --
            # only ``tool.db_path`` / ``tool.config_path`` are
            # needed, and those are pure path properties.
            settings = Settings()
            settings.database.path = tool.db_path

            # Pull HH API defaults from the slice-side schema, not
            # from the (still uninitialised) ``tool.config`` adapter.
            settings.hh_api.base_url = "https://api.hh.ru"
            settings.hh_api.user_agent = "job_bot/0.1.0"
            settings.hh_api.timeout = 30

            # Wire the slice to the tool's actual JSON config file
            # (issue #59): without this, the slice falls back to
            # ``Path("config.json")`` in the current working
            # directory, which is wrong for per-profile setups.
            config_path = tool.config_path / CONFIG_FILENAME

            self._config_auth_slice = create_config_auth_slice(
                settings=settings,
                database=create_database(settings.database.path),
                config_path=config_path,
            )
        return self._config_auth_slice

    def create_config_adapter(self) -> _ConfigAdapter:
        """Create a config adapter that provides the old dict-like interface
        but delegates to the new ConfigAuthSlice.
        """
        if self._config_adapter is None:
            self._config_adapter = _ConfigAdapter(
                slice=self._get_config_auth_slice(),
                tool=self._tool,
            )
        return self._config_adapter

    # ─── Application Prep Slice (VSA) ──────────────────────────────

    def _get_application_prep_slice(self, cover_letter_ai: Any | None = None):
        """Get or create the ApplicationPrepSlice instance (issue #54).

        Args:
            cover_letter_ai: AI client for cover-letter generation. Only
                used on the first call (slice is memoised). If you need
                a slice with a different AI client, invalidate
                ``self._application_prep_slice`` first.
        """
        if self._application_prep_slice is None:
            from job_bot.application_prep.slice import (
                create_application_prep_slice,
            )
            from job_bot.shared.api.client import HHApiClient, HHApiConfig
            from job_bot.shared.config.settings import Settings
            from job_bot.shared.storage.database import create_database

            tool = self._tool
            config = tool.config
            settings = Settings()
            settings.database.path = tool.db_path

            hh_config = config.get("hh_api", {})
            settings.hh_api.base_url = hh_config.get(
                "base_url", "https://api.hh.ru"
            )
            settings.hh_api.user_agent = hh_config.get(
                "user_agent", "job_bot/0.1.0"
            )
            settings.hh_api.timeout = hh_config.get("timeout", 30)

            api_config = HHApiConfig(
                base_url=settings.hh_api.base_url,
                user_agent=settings.hh_api.user_agent,
                timeout=settings.hh_api.timeout,
            )
            api_client = HHApiClient(config=api_config)

            self._application_prep_slice = create_application_prep_slice(
                settings=settings,
                database=create_database(settings.database.path),
                api_client=api_client,
                ai_client=cover_letter_ai,
            )
        return self._application_prep_slice

    def create_application_prep_service(
        self, cover_letter_ai: Any | None = None
    ):
        """Create an adapter that wraps the new ApplicationPrepSlice and
        provides the old ``ApplicationsService``-style interface
        (``prepare_one``) for use by ``PrepareVacanciesUseCase`` (issue #54).

        The adapter still writes to the legacy storage facade so that
        existing tests (and downstream code) that read from
        ``storage.application_drafts`` / ``storage.application_test_answers``
        keep working without migration.

        Args:
            cover_letter_ai: AI client used by the new
                ``CoverLetterHandler`` for AI-generated letters. If ``None``
                (the default), the slice uses template-based letters.
                Passed through from ``prepare_vacancies_use_case`` so the
                ``--use-ai`` CLI flag is honoured.
        """
        if self._application_prep_adapter is None:
            self._application_prep_adapter = _ApplicationPrepAdapter(
                slice=self._get_application_prep_slice(
                    cover_letter_ai=cover_letter_ai
                ),
                storage=self._tool.storage,
            )
        return self._application_prep_adapter

    # ─── Application Submit Slice (VSA) ────────────────────────

    def _get_application_submit_slice(self):
        """Get or create the ApplicationSubmitSlice instance (issue #55).

        Wires the slice against the existing ``tool.session`` /
        ``tool.api_client`` / ``tool.xsrf_token`` so the legacy and
        VSA code paths share the same live connections (no extra
        ``sqlite3.Connection`` is created).
        """
        if self._application_submit_slice is None:
            from job_bot.application_submit.slice import (
                create_application_submit_slice,
            )

            self._application_submit_slice = create_application_submit_slice(
                storage_conn=self._tool.db,
                api_client=self._tool.api_client,
                session=self._tool.session,
                xsrf_token=self._tool.xsrf_token,
            )
        return self._application_submit_slice

    def create_application_submit_adapter(self):
        """Create an adapter that wraps the new ``ApplicationSubmitSlice`` and
        provides the legacy ``apply_one(resume_id, vacancy_id, cover_letter)``
        interface for use by ``ApplyToVacanciesUseCase`` (issue #55).

        The adapter builds an ``ApplicationDraftModel`` from the legacy
        ``params`` dict, saves it to the legacy storage, then delegates
        the actual sending to the slice's :class:`ApplyOnePort`. Returns
        ``True`` on success and ``False`` on ``FatalError`` /
        ``RetryableError`` — matching the legacy boolean contract.
        """
        if self._application_submit_adapter is None:
            self._application_submit_adapter = _ApplicationSubmitAdapter(
                slice=self._get_application_submit_slice(),
                storage=self._tool.storage,
            )
        return self._application_submit_adapter

    # ─── MAX Bot Slice (VSA) ────────────────────────────────

    def _get_max_bot_slice(self):
        """Get or create the :class:`MaxBotSlice` instance (issue #58).

        The slice is intentionally cheap to build (no DB connection, no
        heavy I/O) — we memoise it on the container for symmetry with
        the other slices, even though the operation could rebuild it
        on every run.
        """
        if self._max_bot_slice is None:
            from job_bot.max_bot.requests_transport import RequestsMaxTransport
            from job_bot.max_bot.slice import create_max_bot_slice

            max_cfg = (self._tool.config or {}).get("max") or {}
            bot_token = max_cfg.get("bot_token") or ""
            api_url = (
                max_cfg.get("api_url") or RequestsMaxTransport.DEFAULT_API_URL
            )

            transport = RequestsMaxTransport(
                session=self._tool.session,
                bot_token=bot_token,
                api_url=api_url,
            )
            self._max_bot_slice = create_max_bot_slice(transport=transport)
        return self._max_bot_slice

    def create_max_bot_adapter(self):
        """Create a ``MaxBotSlice`` adapter for the ``max-bot`` CLI operation.

        The adapter exposes the same surface the operation expects
        (``transport``, ``handler``, ``send_message``) — it's just the
        :class:`MaxBotSlice` itself, since the slice's public API is
        already operation-shaped (issue #58). Memoisation is owned by
        :meth:`_get_max_bot_slice`.
        """
        return self._get_max_bot_slice()

    # ─── Telegram Bot Slice (VSA) ───────────────────────────

    def _get_telegram_bot_slice(self):
        """Get or create the :class:`TelegramBotSlice` instance (issue #56).

        Wires the slice against the existing ``tool.session`` /
        ``tool.db`` / ``tool.config`` so the legacy and VSA code paths
        share the same live connections (no extra ``sqlite3.Connection``
        is created). Raises :class:`RuntimeError` mentioning
        ``bot_token`` when the config is missing the field — the
        acceptance test ``test_no_bot_token_raises_clear_error`` relies
        on this.
        """
        if self._telegram_bot_slice is None:
            from job_bot.telegram_bot.slice import create_telegram_bot_slice
            from job_bot.telegram_bot.telegram_transport import (
                TelegramTransport,
                TelegramTransportConfig,
            )

            telegram_cfg = (self._tool.config or {}).get("telegram") or {}
            bot_token = telegram_cfg.get("bot_token") or ""
            if not bot_token:
                raise RuntimeError(
                    "telegram.bot_token is required to build TelegramBotSlice",
                )

            raw_timeout = telegram_cfg.get("poll_timeout", 30)
            try:
                poll_timeout = int(raw_timeout)
            except (ValueError, TypeError):
                poll_timeout = 30
            allowed_raw = telegram_cfg.get("allowed_user_ids") or []
            allowed_user_ids = tuple(int(uid) for uid in allowed_raw)
            proxy_url = telegram_cfg.get("proxy_url")

            transport = TelegramTransport(
                config=TelegramTransportConfig(
                    bot_token=bot_token,
                    poll_timeout=poll_timeout,
                    allowed_user_ids=allowed_user_ids,
                    proxy_url=proxy_url,
                ),
                session=self._tool.session,
            )

            self._telegram_bot_slice = create_telegram_bot_slice(
                database=self._tool.db,
                transport=transport,
                config=self._tool.config,
            )
        return self._telegram_bot_slice

    def create_telegram_bot_adapter(self):
        """Create a :class:`TelegramBotAdapter` for the ``telegram-bot`` CLI.

        The adapter exposes the operation-facing surface
        (``transport``, ``dispatch_update``, ``send_digest``, ``close``,
        ``bot_service``, ``slice``) and is memoised across calls.
        """
        if self._telegram_bot_adapter is None:
            from job_bot.telegram_bot.adapter import create_telegram_bot_adapter

            self._telegram_bot_adapter = create_telegram_bot_adapter(
                slice_=self._get_telegram_bot_slice()
            )
        return self._telegram_bot_adapter

    # ─── Channel Monitoring Slice (VSA) ─────────────────────────────

    def _get_channel_monitor_slice(self):
        """Get or create the :class:`ChannelMonitorSlice` instance (issue #57).

        The slice is intentionally cheap to build (a single
        :class:`ChannelHandler` over ``tool.db``) — memoised for
        symmetry with the other slices.
        """
        if self._channel_monitor_slice is None:
            from job_bot.channel_monitoring.slice import (
                create_channel_monitor_slice,
            )

            self._channel_monitor_slice = create_channel_monitor_slice(
                conn=self._tool.db,
            )
        return self._channel_monitor_slice

    def create_channel_monitor_slice(self):
        """Return the :class:`ChannelMonitorSlice` for the ``channel-monitor`` CLI."""
        # Rationale: the slice is already operation-shaped (exposes
        # ``channels`` / ``handler``), so no wrapping adapter is needed —
        # unlike ``create_telegram_bot_adapter`` / ``create_max_bot_adapter``
        # which return thin adapters to match the legacy CLI surface.
        return self._get_channel_monitor_slice()

    # ─── Use case factories ──────────────────────────────────────

    def apply_to_vacancies_use_case(
        self,
        *,
        system_prompt: str = "",
        use_ai: bool = False,
        send_email: bool = False,
    ) -> ApplyToVacanciesUseCase:
        """Возвращает fully-wired :class:`ApplyToVacanciesUseCase`.

        Args:
            system_prompt: system_prompt для AI-генерации писем.
                Применяется только при ``use_ai=True``.
            use_ai: включить AI для генерации сопроводительных писем.
                При ``False`` письма строятся по шаблону.
            send_email: подключить SMTP-клиент (для отправки писем
                работодателю). При ``False`` ``smtp`` передаётся как
                ``None``.
        """
        tool = self._tool
        return ApplyToVacanciesUseCase(
            api_client=tool.api_client,
            session=tool.session,
            storage=tool.storage,
            cover_letter_ai=(
                tool.get_cover_letter_ai(system_prompt) if use_ai else None
            ),
            captcha_ai=tool.get_captcha_ai(),
            xsrf_token=tool.xsrf_token,
            vacancy_filter_ai_factory=tool.get_vacancy_filter_ai,
            smtp=tool.smtp if send_email else None,
            config=tool.config,
            # Phase 2 ports
            site_parser=self._get_site_parser(),
            email_sender=(self._get_email_sender() if send_email else None),
            test_logger=self._get_test_logger(),
            # Vacancy search service factory (VSA wiring)
            vacancy_search_service_factory=lambda per_page, total_pages: (
                self.create_vacancy_search_adapter(per_page, total_pages)
            ),
        )

    def prepare_vacancies_use_case(
        self,
        *,
        system_prompt: str = "",
        use_ai: bool = False,
    ) -> PrepareVacanciesUseCase:
        """Возвращает fully-wired :class:`PrepareVacanciesUseCase` (issue #5).

        ``prepare-vacancies`` НИКОГДА не отправляет отклики на hh.ru —
        фабрика возвращает use case только с зависимостями для подготовки
        черновиков (поиск вакансий, AI-фильтр, AI-письмо, AI-тесты).

        Args:
            system_prompt: system_prompt для AI-генерации писем.
                Применяется только при ``use_ai=True``.
            use_ai: включить AI для генерации сопроводительных писем
                и ответов на тесты. При ``False`` письма и тесты строятся
                по rule-based fallback.
        """
        tool = self._tool
        cover_letter_ai = (
            tool.get_cover_letter_ai(system_prompt) if use_ai else None
        )
        return PrepareVacanciesUseCase(
            api_client=tool.api_client,
            session=tool.session,
            storage=tool.storage,
            cover_letter_ai=cover_letter_ai,
            vacancy_filter_ai_factory=tool.get_vacancy_filter_ai,
            test_ai=cover_letter_ai,
            # Vacancy search service factory (VSA wiring)
            vacancy_search_service_factory=lambda per_page, total_pages: (
                self.create_vacancy_search_adapter(per_page, total_pages)
            ),
            # Application Prep service factory (VSA wiring, issue #54).
            # The factory closes over ``cover_letter_ai`` so that the
            # underlying ApplicationPrepSlice actually receives the AI
            # client when ``--use-ai`` is passed on the CLI (without
            # this, ``use_ai=True`` would be silently dropped because
            # the adapter would build a no-AI slice).
            application_prep_service_factory=lambda: (
                self.create_application_prep_service(
                    cover_letter_ai=cover_letter_ai
                )
            ),
        )


class _VacancySearchAdapter:
    """Adapter that wraps the new VacancySearchSlice to provide the old
    VacancySearchService interface.
    """

    def __init__(
        self,
        slice: Any,  # VacancySearchSlice
        tool: "HHApplicantTool",
        per_page: int,
        total_pages: int,
    ) -> None:
        self._slice = slice
        self._tool = tool
        self._per_page = per_page
        self._total_pages = total_pages

    def search(
        self,
        search_params: Mapping[str, Any],
        *,
        resume_id: str | None = None,
    ) -> Iterator[Any]:  # SearchVacancy
        """Search vacancies using the new slice for text search,
        falling back to old service for similar_vacancies.
        """
        has_text = bool(search_params.get("text"))

        if has_text:
            # Use new slice for text-based search
            yield from self._search_via_slice(search_params)
        else:
            # Fall back to old service for similar_vacancies (emits deprecation warning)
            yield from self._search_via_old_service(search_params, resume_id)

    def _search_via_slice(
        self, search_params: Mapping[str, Any]
    ) -> Iterator[Any]:
        """Search using the new VacancySearchSlice."""
        # Get current access token from tool's api_client
        access_token = self._tool.api_client.access_token
        if not access_token:
            # If no token, fall back to old service
            yield from self._search_via_old_service(search_params, None)
            return

        # Set token on slice's search port
        search_port = self._slice.search
        search_port.set_access_token(access_token)

        # Call new slice's search_vacancies_raw
        vacancies = search_port.search_vacancies_raw(
            dict(search_params),
            access_token,
            max_pages=self._total_pages,
        )

        # Convert Vacancy list to SearchVacancy iterator
        # The Vacancy model has raw_data which matches SearchVacancy structure
        for vacancy in vacancies:
            yield vacancy.raw_data

    def _search_via_old_service(
        self,
        search_params: Mapping[str, Any],
        resume_id: str | None,
    ) -> Iterator[Any]:
        """Fall back to old VacancySearchService (emits deprecation warning)."""
        from .services import VacancySearchService

        service = VacancySearchService(
            self._tool.api_client,
            per_page=self._per_page,
            total_pages=self._total_pages,
        )
        yield from service.search(search_params, resume_id=resume_id)


class _ApplicationPrepAdapter:
    """Adapter that wraps the new ``ApplicationPrepSlice`` and provides the
    old ``ApplicationsService``-style interface for use by
    ``PrepareVacanciesUseCase`` (issue #54).

    The adapter is intentionally minimal: it forwards AI-filter and
    cover-letter calls to the new slice's ports (``relevance`` /
    ``cover_letters``) while continuing to write the resulting
    ``ApplicationDraftModel`` to the legacy ``StorageFacade`` so that
    downstream code (and the existing test suite) keeps working without
    a storage migration.

    The orchestration itself is intentionally a thin mirror of
    :class:`hh_applicant_tool.services.applications.ApplicationsService.prepare_one`
    — its single purpose is to prove that the new slice is actually
    invoked at runtime in the prepare-vacancies pipeline (acceptance
    criteria of issue #54).
    """

    def __init__(self, slice: Any, storage: Any) -> None:
        self._slice = slice
        self._storage = storage

    # ─── Per-profile AI client injection (issue #54) ────────────────

    def set_filter_ai_client(self, ai_client: Any | None) -> None:
        """Inject the per-profile filter AI client (with system prompt
        baked in via ``vacancy_filter_ai_factory``) into the slice's
        :class:`RelevanceHandler`.

        The handler's ``ai_client`` setter (added in issue #54) takes
        care of routing it to ``is_suitable_heavy`` / ``is_suitable_light``.
        Called by :class:`PrepareVacanciesUseCase` per search profile,
        so the same slice can serve multiple profiles with different
        filter AIs.
        """
        relevance = getattr(self._slice, "relevance", None)
        if relevance is not None:
            relevance.ai_client = ai_client

    def set_cover_letter_ai_client(self, ai_client: Any | None) -> None:
        """Inject the cover-letter AI client (``--use-ai`` flag) into
        the slice's :class:`CoverLetterHandler`.

        Same rationale as :meth:`set_filter_ai_client` — the setter
        avoids the slice-construction-time memoisation gotcha.
        """
        cover_letters = getattr(self._slice, "cover_letters", None)
        if cover_letters is not None:
            cover_letters.ai_client = ai_client

    def prepare_filter_ai_client(
        self,
        profile: Any,
        resume: dict[str, Any],
        factory: Any,
        *,
        rate_limit: Any = None,
    ) -> Any:
        """Build the per-profile filter AI client and inject it into the
        slice's :class:`RelevanceHandler` via :meth:`set_filter_ai_client`.

        Thin wrapper over
        :func:`job_bot.application_prep.utils.build_filter_ai_client`
        (issue #54 dedupe) — both the legacy ``RelevanceService`` path
        and the new VSA path share the same logic.

        Returns the AI client (or ``None`` if no filter is needed /
        available).
        """
        relevance = getattr(self._slice, "relevance", None)
        if relevance is None:
            return None
        return build_filter_ai_client(
            profile=profile,
            resume=resume,
            relevance_obj=relevance,
            factory=factory,
            rate_limit=rate_limit,
        )

    def prepare_one(
        self,
        *,
        resume: dict[str, Any],
        vacancy: dict[str, Any],
        search_profile: Any | None = None,
        resume_analysis: str = "",
        ai_filter_mode: str | None = None,
        placeholders: dict[str, Any] | None = None,
        force_message: bool = False,
        response_url: str | None = None,
    ) -> Any:
        """Prepare a single application draft using the new slice.

        Returns an ``ApplicationDraftModel`` (legacy model) saved into
        the legacy storage, so that callers that re-read the draft via
        ``storage.application_drafts`` keep working.
        """
        # Local import to avoid circular import at module-load time.
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )
        from job_bot.application_prep.utils import analysis_to_dict

        resume_id = resume.get("id")
        vacancy_id = vacancy.get("id")
        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")

        # 1. AI relevance filtering (new slice port)
        relevance_score: int | None = None
        relevance_reason: str | None = None
        analysis_json: dict | None = None
        status = "prepared"

        relevance = getattr(self._slice, "relevance", None)
        if relevance is not None and ai_filter_mode in ("heavy", "light"):
            if ai_filter_mode == "heavy":
                result = relevance.is_suitable_heavy(vacancy)
            else:
                result = relevance.is_suitable_light(vacancy)
            relevance_score = result.score
            relevance_reason = result.reason
            analysis_json = analysis_to_dict(result)
            if not result.suitable:
                status = "rejected"

        # If vacancy rejected by AI - save rejected-draft and exit
        if status == "rejected":
            draft = ApplicationDraftModel(
                search_profile_id=(
                    search_profile.id if search_profile else None
                ),
                resume_id=str(resume_id) if resume_id else "",
                vacancy_id=int(vacancy_id) if vacancy_id else 0,
                employer_id=int(employer_id) if employer_id else None,
                status=status,
                relevance_score=relevance_score,
                relevance_reason=relevance_reason,
                analysis_json=analysis_json,
                full_vacancy_json=vacancy,
                cover_letter=None,
                cover_letter_status=None,
                has_test=bool(vacancy.get("has_test")),
                test_status=None,
            )
            self._storage.application_drafts.save(draft)
            return draft

        # 2. Cover letter generation (new slice port)
        cover_letter: str | None = None
        cover_letter_status: str | None = None
        cover_letters = getattr(self._slice, "cover_letters", None)
        if cover_letters is not None:
            cover_letter = cover_letters.generate_cover_letter(
                vacancy,
                placeholders or {},
                resume_analysis=resume_analysis,
                resume=resume,
                force=force_message,
                required_by_vacancy=bool(
                    vacancy.get("response_letter_required")
                ),
            )
            cover_letter_status = "generated"

        # 3. Tests placeholder (handled by application_submit slice; we
        #    only mirror the manual_required marker so the legacy
        #    ApplicationDraftModel keeps the same surface).
        has_test = bool(vacancy.get("has_test"))
        test_status: str | None = None
        if has_test and not response_url:
            test_status = "manual_required"

        # 4. Save draft to legacy storage
        draft = ApplicationDraftModel(
            search_profile_id=(search_profile.id if search_profile else None),
            resume_id=str(resume_id) if resume_id else "",
            vacancy_id=int(vacancy_id) if vacancy_id else 0,
            employer_id=int(employer_id) if employer_id else None,
            status=status,
            relevance_score=relevance_score,
            relevance_reason=relevance_reason,
            analysis_json=analysis_json,
            full_vacancy_json=vacancy,
            cover_letter=cover_letter,
            cover_letter_status=cover_letter_status,
            has_test=has_test,
            test_status=test_status,
        )
        self._storage.application_drafts.save(draft)
        return draft


class _ApplicationSubmitAdapter:
    """Adapter that wraps the new ``ApplicationSubmitSlice`` and provides
    the legacy ``apply_one(resume_id, vacancy_id, cover_letter)``
    interface for use by ``ApplyToVacanciesUseCase`` (issue #55).

    Builds an ``ApplicationDraftModel`` from the legacy ``params`` dict,
    saves it to the legacy storage facade, then delegates the actual
    sending to the slice's :class:`ApplyOnePort`
    (``slice.apply_one`` → :class:`ApplyOneHandler`). Translates the
    slice's exception contract (``FatalError`` → ``False``,
    ``RetryableError`` → ``False`` with warning) into the boolean
    contract expected by the legacy ``_send_apply_request`` path.
    """

    def __init__(self, slice: Any, storage: Any) -> None:
        self._slice = slice
        self._storage = storage

    def apply_one(
        self,
        *,
        resume_id: str,
        vacancy_id: str | int,
        cover_letter: str = "",
        vacancy: dict[str, Any] | None = None,
        search_profile_id: int | None = None,
    ) -> bool:
        """Submit a single draft via the new slice.

        Returns ``True`` on success, ``False`` on
        :class:`RetryableError` (caller may retry). Re-raises
        :class:`FatalError`, :class:`CaptchaRequired`,
        :class:`LimitExceeded` and other :class:`ApiError` so the
        surrounding ``_apply_to_resume`` loop's exception handlers
        (limit-break, captcha-retry) fire — matches the legacy
        ``api_client.post`` contract.
        """
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )
        from job_bot.application_submit.errors import (
            FatalError,
            RetryableError,
        )

        draft = ApplicationDraftModel(
            search_profile_id=search_profile_id,
            resume_id=str(resume_id) if resume_id else "",
            vacancy_id=int(vacancy_id) if vacancy_id else 0,
            status="pending",
            cover_letter=cover_letter,
            full_vacancy_json=vacancy or {},
            has_test=bool((vacancy or {}).get("has_test")),
        )
        if draft.hh_response_url is None:
            draft.hh_response_url = f"https://hh.ru/vacancy/{draft.vacancy_id}"

        try:
            self._slice.apply_one(draft)
        except FatalError:
            # Re-raise: legacy contract re-raised FatalError / ApiError
            # so the surrounding loop's exception handlers fire.
            raise
        except RetryableError as ex:
            logger.warning("application_submit adapter: RetryableError: %s", ex)
            return False

        # Save the draft once on success (no pre-save dead state).
        draft.status = "applied"
        draft.last_error = None
        self._storage.application_drafts.save(draft)
        self._storage.application_drafts.commit()
        return True


def _analysis_to_dict(result: Any) -> dict:
    """Backward-compat shim — delegates to the shared utility.

    Issue #54: kept as a thin wrapper so that any external callers (or
    older tests) importing ``_analysis_to_dict`` from this module still
    work. New code should import from
    :func:`job_bot.application_prep.utils.analysis_to_dict`.
    """
    from job_bot.application_prep.utils import analysis_to_dict

    return analysis_to_dict(result)


class _ConfigAdapter:
    """Adapter that wraps the new ConfigAuthSlice to provide the old
    dict-like config interface.
    """

    def __init__(
        self,
        slice: Any,  # ConfigAuthSlice
        tool: "HHApplicantTool",
    ) -> None:
        self._slice = slice
        self._tool = tool
        self._config_handler = slice.config
        self._config_path = slice.config_path
        self._cached_config: dict[str, Any] | None = None

    def _load_config(self) -> dict[str, Any]:
        """Load config from the new slice and convert to flat dict.

        The old config format was flat with keys like:
        - client_id, client_secret, user_agent, api_delay
        - token (with access_token, refresh_token, access_expires_at)
        - hh_api (with base_url, timeout, etc.)
        - telegram, ai, max, smtp (nested)

        The new format is nested under section names (hh, telegram, etc.).
        This method flattens the new format to match the old interface.
        """
        if self._cached_config is None:
            app_config = self._config_handler.load(self._config_path)
            new_format = app_config.to_dict()

            # Flatten to old format
            flat = {}

            # HH config -> top level keys (legacy)
            hh = new_format.get("hh", {})
            if hh:
                flat["client_id"] = hh.get("client_id")
                flat["client_secret"] = hh.get("client_secret")
                flat["user_agent"] = hh.get("user_agent")
                flat["api_delay"] = hh.get("api_delay", 0.345)
                flat["redirect_uri"] = hh.get("redirect_uri")
                flat["scope"] = hh.get("scope")
                # hh_api section for base_url, timeout
                flat["hh_api"] = {
                    "base_url": hh.get("base_url", "https://api.hh.ru"),
                    "timeout": hh.get("timeout", 30),
                }

            # Telegram config
            telegram = new_format.get("telegram", {})
            if telegram:
                flat["telegram"] = telegram

            # AI config
            ai = new_format.get("ai", {})
            if ai:
                flat["ai"] = ai
                # Also support openai_cover_letter etc. as aliases
                flat["openai_cover_letter"] = ai

            # MAX config
            max_cfg = new_format.get("max", {})
            if max_cfg:
                flat["max"] = max_cfg

            # SMTP config
            smtp = new_format.get("smtp", {})
            if smtp:
                flat["smtp"] = smtp

            # Profiles
            profiles = new_format.get("profiles", {})
            if profiles:
                flat["profiles"] = profiles

            # Active profile
            active_profile = new_format.get("active_profile")
            if active_profile:
                flat["active_profile"] = active_profile

            # Token is not in AppConfig (handled by auth_handler)
            # But we provide empty token dict for compatibility
            flat["token"] = {}

            self._cached_config = flat
        return self._cached_config

    def _invalidate_cache(self) -> None:
        """Invalidate the cached config."""
        self._cached_config = None

    # Dict-like interface
    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by key (supports nested keys with dots)."""
        config = self._load_config()
        # Handle nested keys like 'telegram.bot_token'
        if "." in key:
            parts = key.split(".")
            value = config
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return default
            return value
        return config.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """Dict-style access."""
        value = self.get(key)
        if value is None and key not in self._load_config():
            raise KeyError(key)
        return value

    def __contains__(self, key: str) -> bool:
        """Check if key exists in config."""
        config = self._load_config()
        if "." in key:
            parts = key.split(".")
            value = config
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return False
            return True
        return key in config

    def __iter__(self):
        """Iterate over config keys."""
        return iter(self._load_config())

    def __len__(self) -> int:
        """Return number of top-level config keys."""
        return len(self._load_config())

    def __repr__(self) -> str:
        """String representation."""
        return f"_ConfigAdapter({self._config_path})"

    # Config methods
    def load(self) -> None:
        """Reload config from disk (invalidates cache)."""
        self._invalidate_cache()
        self._load_config()

    def save(self, **kwargs: Any) -> None:
        """Save config updates to disk.

        Accepts keyword arguments that can be either:
        - Top-level keys: client_id, client_secret, etc.
        - Nested dicts: telegram={'bot_token': '...'}, smtp={...}, etc.

        Note (issue #59): the ``token=...`` kwarg is **not** supported
        here -- ``AppConfig`` has no ``token`` field, so a token
        passed to ``save()`` would be silently dropped. Use
        :meth:`save_token` instead, which routes to
        ``slice.auth.save_credentials()``.
        """
        # Load current config
        app_config = self._config_handler.load(self._config_path)
        current_dict = app_config.to_dict()

        # Merge updates
        for key, value in kwargs.items():
            if key == "token":
                # Defensive: explicitly skip the legacy ``token=``
                # kwarg to make the contract gap visible at runtime
                # rather than silently dropping it (see
                # :meth:`save_token` for the auth-aware path).
                # Use ``warnings.warn`` (not ``logger.warning``) so
                # the gap is greppable via ``warnings.catch_warnings``
                # and matches the spirit of issue #70's
                # module-level ``DeprecationWarning``.
                warnings.warn(
                    "_ConfigAdapter.save(token=...) is a no-op under the "
                    "VSA slice; use save_token() to persist OAuth "
                    "credentials via slice.auth.save_credentials().",
                    DeprecationWarning,
                    stacklevel=2,
                )
                continue
            if (
                isinstance(value, dict)
                and key in current_dict
                and isinstance(current_dict[key], dict)
            ):
                # Merge nested dict
                current_dict[key].update(value)
            else:
                # Replace or add top-level key
                current_dict[key] = value

        # Convert back to AppConfig and save
        from job_bot.config_auth.models.config import AppConfig

        new_config = AppConfig.from_dict(current_dict)
        self._config_handler.save(new_config, self._config_path, backup=True)
        self._invalidate_cache()

    def save_token(
        self,
        token: dict[str, Any],
        *,
        profile_id: str | None = None,
    ) -> None:
        """Persist OAuth credentials through the VSA auth port (issue #59).

        Replaces the legacy ``utils.Config.save(token=...)`` pattern,
        which silently no-ops under the VSA ``AppConfig`` (the
        ``AppConfig`` schema has no ``token`` field -- the auth
        handler is the source of truth for credentials, stored in
        the ``oauth_credentials`` SQLite table). Routes to
        :meth:`ConfigAuthSlice.auth.save_credentials`.

        Args:
            token: dict with ``access_token``, ``refresh_token`` and
                ``access_expires_at`` keys (matches
                :meth:`OAuthCredentials.from_dict`).
            profile_id: optional profile id to persist under. Falls
                back to ``self._tool.profile_id`` (the active CLI
                profile) so multi-profile setups (issue #62) keep
                their tokens per-profile. Defaults to ``"default"``
                when neither is set.
        """
        from job_bot.config_auth.models.credentials import OAuthCredentials

        if profile_id is None:
            profile_id = getattr(self._tool, "profile_id", None) or "default"
        credentials = OAuthCredentials.from_dict(token)
        self._slice.auth.save_credentials(credentials, profile_id=profile_id)

    # For compatibility with dict()
    def keys(self):
        return self._load_config().keys()

    def values(self):
        return self._load_config().values()

    def items(self):
        return self._load_config().items()
