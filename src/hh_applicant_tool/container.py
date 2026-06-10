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

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from .application import ApplyToVacanciesUseCase, PrepareVacanciesUseCase

if TYPE_CHECKING:
    from .main import HHApplicantTool


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
            vacancy_search_service_factory=lambda per_page, total_pages: self.create_vacancy_search_adapter(per_page, total_pages),
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
            vacancy_search_service_factory=lambda per_page, total_pages: self.create_vacancy_search_adapter(per_page, total_pages),
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
