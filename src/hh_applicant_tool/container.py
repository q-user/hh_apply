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

from typing import TYPE_CHECKING

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
        )
