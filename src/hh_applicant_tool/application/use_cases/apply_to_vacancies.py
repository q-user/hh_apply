"""Use case: отклик на вакансии (apply).

Извлечено из ``operations/apply_vacancies.py`` (issue #15). Use case
владеет полным циклом рассылки откликов, который раньше жил в
``Operation._apply_vacancies`` / ``Operation._apply_resume``. Зависимости
(API client, session, storage, AI-клиенты, SMTP, конфиг) принимаются
через конструктор — use case не зависит от ``HHApplicantTool`` service
locator и может быть вызван из CLI, UI, prepare-vacancies (#5),
apply-worker (#10) или Telegram-бота (#7-9).

Phase 2 (Clean Architecture): порты для инфраструктурных зависимостей
(captcha, site parsing, email, cancellation, clock, vacancy fetcher, test
logger, delay) принимаются опционально через конструктор. Если порт не
передан — используется legacy-код напрямую (с обратной совместимостью).
"""

from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import smtplib
import sqlite3
import threading
from datetime import datetime
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any, Callable, cast

import requests

from ...ai.base import AIError
from ...api import BadResponse, Redirect
from ...api.datatypes import SearchVacancy
from ...api.errors import ApiError, CaptchaRequired, LimitExceeded
from ...services import (
    DEFAULT_LETTER_TEMPLATE,
    CoverLetterService,
    RelevanceService,
    VacancySearchService,
    build_filter_system_prompt_heavy,
    build_filter_system_prompt_light,
    build_search_params,
)
from ...storage.repositories.errors import RepositoryError
from ...utils.json import JSONDecoder
from ...utils.string import rand_text, strip_tags, unescape_string
from ..dto import ApplyToVacanciesCommand, ApplyToVacanciesResult

if TYPE_CHECKING:
    from ...utils.config import Config
    from ..ports import (
        CancellationToken,
        CaptchaSolverPort,
        Clock,
        EmailSenderPort,
        SiteParserPort,
        TestVacancyLoggerPort,
    )

logger = logging.getLogger(__package__)


ProgressCallback = Callable[[str], None]


class ApplyToVacanciesUseCase:
    """Оркестратор отклика на вакансии (apply).

    Use case не знает про argparse, ``HHApplicantTool`` или UI. Он
    получает «сырые» зависимости через конструктор, конструирует
    внутренние сервисы (``CoverLetterService``,
    ``VacancySearchService``, ``RelevanceService``) и оркестрирует
    рассылку. На выходе — ``ApplyToVacanciesResult`` со статистикой.

    Attributes:
        api_client: ``api.client.ApiClient`` — HTTP-клиент HH API.
        session: ``requests.Session`` — низкоуровневая сессия
            (используется для капчи, парсинга сайтов работодателей,
            ``hh.ru/vacancy/...`` raw HTML).
        storage: ``storage.StorageFacade`` — фасад локальной БД.
        cover_letter_ai: ``ai.ChatOpenAI`` с system_prompt для
            генерации писем или ``None`` (тогда письмо по шаблону).
        captcha_ai: ``ai.ChatOpenAI`` для распознавания капчи.
        xsrf_token: XSRF-токен текущей сессии (используется
            ``VacancyTestsService`` при отправке тестовых откликов).
        smtp: ``smtplib.SMTP`` клиент или ``None``.
        config: ``utils.Config`` (для шаблонов ``apply_mail_*``
            и секции ``smtp``).

        (Phase 2 ports — опционально, заменяют прямые вызовы)
        captcha_solver: ``CaptchaSolverPort`` — решает капчу (issue #38).
        site_parser: ``SiteParserPort`` — парсит сайты работодателей (issue #34).
        email_sender: ``EmailSenderPort`` — отправляет email (issue #36).
        cancellation: ``CancellationToken`` — токен отмены (issue #24).
        clock: ``Clock`` — операции со временем (issue #25).
        test_logger: ``TestVacancyLoggerPort`` — логирует вакансии с тестами (issue #30).
    """

    SEL_CAPTCHA_IMAGE = 'img[data-qa="account-captcha-picture"]'
    SEL_CAPTCHA_INPUT = 'input[data-qa="account-captcha-input"]'

    def __init__(
        self,
        api_client: Any,
        session: requests.Session,
        storage: Any,
        cover_letter_ai: Any,
        captcha_ai: Any,
        xsrf_token: str,
        *,
        vacancy_filter_ai: Any = None,
        vacancy_filter_ai_factory: Callable[[str], Any] | None = None,
        smtp: Any = None,
        config: "Config | None" = None,
        # Phase 2 ports (optional, backward compatible)
        captcha_solver: "CaptchaSolverPort | None" = None,
        site_parser: "SiteParserPort | None" = None,
        email_sender: "EmailSenderPort | None" = None,
        cancellation: "CancellationToken | None" = None,
        clock: "Clock | None" = None,
        test_logger: "TestVacancyLoggerPort | None" = None,
        # Vacancy search service (optional, for VSA wiring)
        vacancy_search_service_factory: Any = None,
        # Application submit adapter (optional, for VSA wiring — issue #55)
        application_submit_adapter: Any = None,
    ) -> None:
        self.api_client = api_client
        self.session = session
        self.storage = storage
        self.cover_letter_ai = cover_letter_ai
        self.captcha_ai = captcha_ai
        self.xsrf_token = xsrf_token
        self.vacancy_filter_ai = vacancy_filter_ai
        self.vacancy_filter_ai_factory = vacancy_filter_ai_factory
        self.smtp = smtp
        self.config = config

        # Phase 2 ports
        self._captcha_solver = captcha_solver
        self._site_parser = site_parser
        self._email_sender = email_sender
        self._cancellation = cancellation
        self._clock = clock
        self._test_logger = test_logger

        # Состояние, заполняемое в execute().
        # ``command``, ``cover_letter_service``, ``vacancy_search_service``
        # и ``relevance_service`` обязательно проставляются в :meth:`execute`
        # -- это единственный публичный вход use case'а, поэтому mypy видит
        # атрибуты без ``| None`` и все обращения ``self.command.X``,
        # ``self.vacancy_search_service.Y`` и ``self.relevance_service.Z``
        # ниже не нуждаются в ``# type: ignore[union-attr]``.
        self.command: ApplyToVacanciesCommand
        self.cancel_event: threading.Event | None = None
        self.progress_callback: ProgressCallback | None = None
        self.cover_letter_service: CoverLetterService
        self.vacancy_search_service: VacancySearchService
        self.relevance_service: RelevanceService

        # Внедрённый сервис поиска вакансов (VSA wiring)
        self._injected_vacancy_search_service_factory = vacancy_search_service_factory

        # Внедрённый адаптер отправки откликов (VSA wiring, issue #55).
        # Если не передан — используется legacy-путь в
        # :meth:`_send_apply_request` (прямой ``api_client.post``).
        self._application_submit_adapter = application_submit_adapter

        # Кеш/инструменты.
        self.json_decoder = JSONDecoder()

    # ─── Публичный API ───────────────────────────────────────────

    def execute(
        self,
        command: ApplyToVacanciesCommand,
        *,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ApplyToVacanciesResult:
        """Запускает рассылку откликов.

        Args:
            command: входные параметры (``ApplyToVacanciesCommand``).
            cancel_event: опциональное событие отмены (UI ставит его
                при нажатии «Отменить»).
            progress_callback: опциональный колбэк прогресса.
                Вызывается с теми же строками, что выводятся в stdout,
                — для интеграции с UI, отличным от redirect_stdout.

        Returns:
            ``ApplyToVacanciesResult`` со статистикой рассылки.
        """
        self.command = command
        self.cancel_event = cancel_event
        self.progress_callback = progress_callback

        # Конструируем внутренние сервисы.
        cover_letter_template = (
            command.letter_file_content or DEFAULT_LETTER_TEMPLATE
        )
        self.cover_letter_service = CoverLetterService(
            self.api_client,
            self.cover_letter_ai,
            template=cover_letter_template,
        )
        # Use injected vacancy search service factory (VSA wiring) or fall back to old service
        if self._injected_vacancy_search_service_factory is not None:
            self.vacancy_search_service = self._injected_vacancy_search_service_factory(
                command.per_page, command.total_pages
            )
        else:
            self.vacancy_search_service = VacancySearchService(
                self.api_client,
                per_page=command.per_page,
                total_pages=command.total_pages,
            )
        self.relevance_service = RelevanceService(
            self.api_client,
            ai_client=None,
            relevance_rules=(
                self.command.relevance_rules
            ),
        )

        result = ApplyToVacanciesResult()

        resumes = self._fetch_published_resumes(command.resume_id)
        if not resumes:
            logger.warning("У вас нет опубликованных резюме")
            return result

        me = self._fetch_me()
        seen_employers: set[str] = set()

        for resume in resumes:
            result.resumes_processed += 1
            applied, limit_reached = self._apply_to_resume(
                resume=resume,
                user=me,
                seen_employers=seen_employers,
            )
            result.applied += applied
            if limit_reached:
                result.limit_reached = True
                logger.warning(
                    "Лимит откликов hh.ru исчерпан. "
                    "Пропускаю оставшиеся резюме."
                )
                self._notify(
                    "⛔ Лимит откликов hh.ru исчерпан. Попробуйте позже."
                )
                break

        return result

    # ─── Внутренние помощники оркестрации ────────────────────────

    def _fetch_published_resumes(
        self, resume_id: str | None
    ) -> list[dict[str, Any]]:
        """Загружает резюме пользователя, сохраняет в storage, фильтрует
        по ``resume_id`` (если задан) и статусу ``published``.
        """
        resumes: list[dict[str, Any]] = (
            self.api_client.get("/resumes/mine").get("items") or []
        )
        try:
            self.storage.resumes.save_batch(resumes)
        except RepositoryError as ex:
            logger.exception(ex)

        if resume_id:
            resumes = list(filter(lambda x: x["id"] == resume_id, resumes))
        resumes = list(
            filter(lambda x: x["status"]["id"] == "published", resumes)
        )
        return resumes

    def _fetch_me(self) -> dict[str, Any]:
        return self.api_client.get("/me")

    def _apply_to_resume(
        self,
        resume: dict[str, Any],
        user: dict[str, Any],
        seen_employers: set[str],
    ) -> tuple[int, bool]:
        """Оркестратор рассылки откликов для одного резюме.

        Returns:
            ``(applied_count, limit_reached)``.
        """
        logger.info(
            "Начинаю рассылку откликов для резюме: %s (%s)",
            resume["alternate_url"],
            resume["title"],
        )
        self._notify(
            "[START] Начинаю рассылку откликов для резюме:",
            resume["title"],
        )

        placeholders = {
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "email": user.get("email") or "",
            "phone": user.get("phone") or "",
            "resume_hash": resume.get("id") or "",
            "resume_title": resume.get("title") or "",
            "resume_url": resume.get("alternate_url") or "",
        }

        do_apply = True
        applied_count = 0
        limit_reached = False
        site_emails: dict[str, Any] = {}
        resume_analysis = self._init_ai_filter(resume)

        max_responses = self.command.max_responses

        for vacancy in self._get_vacancies(resume_id=resume["id"]):
            if self._is_cancelled():
                logger.info("Операция отменена пользователем")
                break
            if max_responses is not None and applied_count >= max_responses:
                logger.info(
                    "Достигнут лимит откликов (max_responses=%d). Останавливаю.",
                    max_responses,
                )
                break
            try:
                if self._check_vacancy_skips(vacancy, resume, do_apply):
                    continue

                self._save_vacancy_to_storage(vacancy)

                self._load_employer_profile(
                    vacancy, seen_employers, site_emails
                )

                message_placeholders = self._build_message_placeholders(
                    vacancy, placeholders
                )
                letter = self._generate_cover_letter(
                    vacancy, message_placeholders, resume_analysis, resume
                )
                logger.debug(letter)

                if vacancy.get("has_test"):
                    self._handle_vacancy_test(vacancy, resume["id"])
                    continue

                params = {
                    "resume_id": resume["id"],
                    "vacancy_id": vacancy["id"],
                    "message": letter,
                }
                logger.debug(
                    "Пробуем откликнуться на вакансию: %s",
                    vacancy["alternate_url"],
                )
                if self._send_apply_request(params, vacancy):
                    applied_count += 1

                self._maybe_send_email(
                    vacancy,
                    vacancy.get("employer", {}).get("id"),
                    message_placeholders,
                    site_emails,
                )
            except LimitExceeded:
                do_apply = False
                limit_reached = True
                logger.warning(
                    "Достигли лимита на отклики (отправлено в этой сессии: %d)",
                    applied_count,
                )
                break
            except ApiError as ex:
                logger.warning(ex)
            except (BadResponse, AIError) as ex:
                logger.error(ex)

        logger.info(
            "Закончили рассылку откликов для резюме: %s (%s). Отправлено: %d",
            resume["alternate_url"],
            resume["title"],
            applied_count,
        )
        self._notify(
            f"[DONE] Закончили рассылку для резюме: {resume['title']}. "
            f"Отправлено: {applied_count}"
        )
        return applied_count, limit_reached

    # ─── Прогресс-канал ──────────────────────────────────────────

    def _notify(self, *args: Any) -> None:
        """Печатает сообщение в stdout и (опционально) дёргает
        ``progress_callback`` — единственный внешний канал прогресса.
        """
        message = " ".join(str(a) for a in args)
        print(message)
        if self.progress_callback is not None:
            try:
                self.progress_callback(message)
            except Exception as ex:  # noqa: BLE001
                # User-provided callback (UI progress). Any failure must
                # not crash the apply loop — log and continue.
                logger.warning("progress_callback error: %s", ex)

    def _now(self) -> datetime:
        """Возвращает текущее время через ``Clock`` порт (issue #25)
        или ``datetime.now()``."""
        if self._clock is not None:
            return self._clock.now()
        return datetime.now()

    def _is_cancelled(self) -> bool:
        """Проверяет отмену через ``CancellationToken`` (issue #24)
        или ``threading.Event``."""
        if self._cancellation is not None:
            return self._cancellation.is_cancelled
        return self.cancel_event is not None and self.cancel_event.is_set()

    # ─── AI-фильтр (per-resume) ─────────────────────────────────

    def _init_ai_filter(self, resume: dict[str, Any]) -> str:
        """Инициализирует AI-фильтр вакансий (heavy/light).

        Если ``command.ai_filter`` задан, через
        ``vacancy_filter_ai_factory`` (или напрямую
        ``self.vacancy_filter_ai``) создаётся AI-клиент с нужным
        ``system_prompt``, и его ``rate_limit`` подгоняется под
        ``command.ai_rate_limit``. Возвращает текст анализа резюме
        (используется CoverLetterService).

        Returns:
            Текст анализа резюме (resume_analysis) — используется
            ``CoverLetterService`` при AI-генерации письма.
        """
        ai_filter = self.command.ai_filter
        if not ai_filter:
            return ""

        if ai_filter == "heavy":
            resume_analysis = self.relevance_service.analyze_resume_heavy(
                resume
            )
            system_prompt = build_filter_system_prompt_heavy(
                resume_analysis,
                relevance_rules=self.relevance_service.relevance_rules,
            )
        elif ai_filter == "light":
            resume_analysis = self.relevance_service.analyze_resume_light(
                resume
            )
            system_prompt = build_filter_system_prompt_light(
                resume_analysis,
                relevance_rules=self.relevance_service.relevance_rules,
            )
        else:
            raise ValueError(f"Неизвестный режим AI фильтра: {ai_filter}")

        logger.debug("AI системный промпт (%s): %s", ai_filter, system_prompt)

        if self.vacancy_filter_ai_factory is not None:
            self.vacancy_filter_ai = self.vacancy_filter_ai_factory(
                system_prompt
            )
        elif self.vacancy_filter_ai is None:
            raise ValueError(
                "AI фильтр включён, но ни vacancy_filter_ai, "
                "ни vacancy_filter_ai_factory не заданы"
            )

        if self.command.ai_rate_limit and self.vacancy_filter_ai is not None:
            self.vacancy_filter_ai.rate_limit = self.command.ai_rate_limit
        self.relevance_service.ai_client = self.vacancy_filter_ai
        return resume_analysis

    # ─── Skip policy ─────────────────────────────────────────────

    def _check_vacancy_skips(
        self,
        vacancy: SearchVacancy,
        resume: dict[str, Any],
        do_apply: bool,
    ) -> str | None:
        """Проверяет все условия пропуска вакансии.

        Returns:
            Строка-причина пропуска или ``None``, если вакансия ОК.
        """
        if not do_apply:
            return "limit_reached"
        relations = vacancy.get("relations", [])
        if relations:
            logger.debug(
                "Пропускаем вакансию с откликом: %s",
                vacancy["alternate_url"],
            )
            if "got_rejection" in relations:
                logger.debug(
                    "Вы получили отказ от %s", vacancy["alternate_url"]
                )
                self._notify("⛔ Пришел отказ от", vacancy["alternate_url"])
            return "already_responded"
        if vacancy.get("archived"):
            logger.debug(
                "Пропускаем вакансию в архиве: %s",
                vacancy["alternate_url"],
            )
            return "archived"
        if (
            vacancy.get("has_test") and self.command.skip_tests
        ):
            logger.debug(
                "Пропускаю вакансию с тестом %s",
                vacancy["alternate_url"],
            )
            return "has_test"
        if redirect_url := vacancy.get("response_url"):
            logger.debug(
                "Пропускаем вакансию %s с перенаправлением: %s",
                vacancy["alternate_url"],
                redirect_url,
            )
            return "redirected"
        if self._is_excluded(vacancy):
            logger.info(
                "Вакансия попала под фильтр: %s",
                vacancy["alternate_url"],
            )
            self._save_skipped_vacancy(vacancy, "excluded_filter", resume["id"])
            self.api_client.put(f"/vacancies/blacklisted/{vacancy['id']}")
            logger.info(
                "Вакансия добавлена в черный список: %s",
                vacancy["alternate_url"],
            )
            return "excluded"

        # AI фильтрация
        ai_filter = self.command.ai_filter
        if ai_filter and self.vacancy_filter_ai is not None:
            if self._is_vacancy_already_skipped(vacancy, resume["id"]):
                logger.debug(
                    "Вакансия уже была отклонена ранее: %s",
                    vacancy["alternate_url"],
                )
                self._notify(
                    ">> Вакансия уже отклонена ранее",
                    vacancy["alternate_url"],
                )
                return "ai_already_skipped"
            if ai_filter == "heavy":
                is_suitable = self.relevance_service.is_suitable_heavy(
                    cast(dict[str, Any], vacancy)
                ).suitable
            elif ai_filter == "light":
                is_suitable = self.relevance_service.is_suitable_light(
                    cast(dict[str, Any], vacancy)
                ).suitable
            else:
                raise ValueError(f"Неизвестный режим AI фильтра: {ai_filter}")
            if not is_suitable:
                logger.info(
                    "Вакансия отклонена AI фильтром (%s): %s",
                    ai_filter,
                    vacancy["alternate_url"],
                )
                self._notify(
                    f"[AI] ({ai_filter}) посчитал неподходящей",
                    vacancy["alternate_url"],
                )
                self._save_skipped_vacancy(vacancy, "ai_rejected", resume["id"])
                return "ai_rejected"
        return None

    # ─── Хранилище ───────────────────────────────────────────────

    def _save_vacancy_to_storage(self, vacancy: SearchVacancy) -> None:
        try:
            self.storage.vacancies.save(vacancy)
        except RepositoryError as ex:
            logger.debug(ex)
        if vacancy.get("contacts"):
            logger.debug(
                f"Найдены контакты в вакансии: {vacancy['alternate_url']}"
            )
            try:
                self.storage.vacancy_contacts.save(vacancy)
            except RepositoryError as ex:
                logger.exception(ex)

    def _save_skipped_vacancy(
        self,
        vacancy: SearchVacancy,
        reason: str,
        resume_id: str | None = None,
    ) -> None:
        try:
            employer = vacancy.get("employer") or {}
            self.storage.skipped_vacancies.save(
                {
                    "resume_id": resume_id or "",
                    "vacancy_id": vacancy["id"],
                    "reason": reason,
                    "alternate_url": vacancy.get("alternate_url"),
                    "name": vacancy.get("name"),
                    "employer_name": employer.get("name"),
                    "created_at": self._now(),
                }
            )
        except (RepositoryError, sqlite3.Error) as ex:
            logger.warning(f"Не удалось сохранить пропущенную вакансию: {ex}")

    def _is_vacancy_already_skipped(
        self, vacancy: SearchVacancy, resume_id: str | None = None
    ) -> bool:
        try:
            vacancy_id = vacancy["id"]
            if resume_id:
                if any(
                    self.storage.skipped_vacancies.find(
                        resume_id=resume_id,
                        vacancy_id=vacancy_id,
                    )
                ):
                    return True
            return any(
                self.storage.skipped_vacancies.find(
                    resume_id="",
                    vacancy_id=vacancy_id,
                )
            )
        except (RepositoryError, sqlite3.Error):
            return False

    # ─── Профиль работодателя + парсинг сайта ────────────────────

    def _load_employer_profile(
        self,
        vacancy: SearchVacancy,
        seen_employers: set[str],
        site_emails: dict[str, Any],
    ) -> None:
        """Загружает профиль работодателя и парсит его сайт на email'ы.

        Мутирует ``site_emails[employer_id]`` в случае успешного парсинга.
        """
        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")
        if not employer_id or employer_id in seen_employers:
            return
        employer_profile = self.api_client.get(f"/employers/{employer_id}")
        try:
            self.storage.employers.save(employer_profile)
        except RepositoryError as ex:
            logger.exception(ex)
        if not (
            self.command.send_email
            and (site_url := (employer_profile.get("site_url") or "").strip())
        ):
            return
        site_url = site_url if "://" in site_url else "https://" + site_url
        logger.debug("visit site: %s", site_url)
        try:
            site_info = self._parse_site(site_url)
            site_emails[employer_id] = site_info["emails"]
        except requests.RequestException as ex:
            site_info = None
            logger.error(ex)
        if site_info:
            logger.debug("site info: %r", site_info)
            try:
                self.storage.employer_sites.save(
                    {
                        "site_url": site_url,
                        "employer_id": employer_id,
                        "subdomains": [],
                        **site_info,
                    }
                )
            except RepositoryError as ex:
                logger.exception(ex)

    def _parse_site(self, url: str) -> dict[str, Any]:
        """Парсит сайт работодателя.

        Предпочитает ``SiteParserPort`` (issue #34);
        fallback — прямой ``session.get()`` с regex.
        """
        if self._site_parser is not None:
            try:
                return self._site_parser.parse_site(url)
            except Exception as ex:  # noqa: BLE001
                # User-provided port (SiteParserPort). Any failure must
                # not crash the apply loop — log and fall through to
                # the legacy regex-based parser below.
                logger.warning("SiteParserPort failed for %s: %s", url, ex)

        # Legacy fallback
        with self.session.get(url, timeout=10) as r:
            val: Callable[[re.Match[str] | None], str] = (
                lambda m: html.unescape(m.group(1)) if m else ""
            )

            title = val(re.search(r"<title>(.*?)</title>", r.text, re.I | re.S))
            description = val(
                re.search(
                    r'<meta name="description" content="(.*?)"',
                    r.text,
                    re.I,
                )
            )
            generator = val(
                re.search(
                    r'<meta name="generator" content="(.*?)"',
                    r.text,
                    re.I,
                )
            )

            emails = set(
                m.group(0)
                # Исключение всякого мусора типа
                # energy-software-slider-225x225@2x.png
                for m in re.finditer(
                    r"\b[a-z][a-z0-9_.-]+@("
                    r"[a-z0-9][a-z0-9-]+)(?!\.(?:png|jpe?g|bmp|gif|ico|"
                    r"js|css)\b)(\.[a-z0-9][a-z0-9-]+)+\b",
                    r.text,
                )
            )

            return {
                "title": title,
                "description": description,
                "generator": generator,
                "emails": list(emails),
                "server_name": r.headers.get("Server"),
                "powered_by": r.headers.get("X-Powered-By"),
                "ip_address": r.raw._connection.sock.getpeername()[0]
                if r.raw._connection
                else None,
            }

    # ─── Письма ──────────────────────────────────────────────────

    @staticmethod
    def _build_message_placeholders(
        vacancy: SearchVacancy, placeholders: dict[str, Any]
    ) -> dict[str, Any]:
        employer = vacancy.get("employer") or {}
        return {
            "vacancy_name": vacancy.get("name", ""),
            "employer_name": employer.get("name", ""),
            **placeholders,
        }

    def _generate_cover_letter(
        self,
        vacancy: SearchVacancy,
        message_placeholders: dict[str, Any],
        resume_analysis: str,
        resume: dict[str, Any],
    ) -> str:
        return self.cover_letter_service.generate(
            cast(dict[str, Any], vacancy),
            message_placeholders,
            resume_analysis=resume_analysis,
            resume=resume,
            force=self.command.force_message,  # type: ignore[union-attr]
            required_by_vacancy=bool(vacancy.get("response_letter_required")),
        )

    def _handle_vacancy_test(
        self, vacancy: SearchVacancy, resume_id: str
    ) -> None:
        """Обрабатывает вакансию с тестом: логирует, сохраняет в файл
        ``vacancies_with_tests.txt``, помечает как skipped."""
        test_link = vacancy.get("alternate_url")
        employer = vacancy.get("employer") or {}
        logger.info("Найдена вакансия с тестом: %s", test_link)

        if self._test_logger is not None:
            try:
                self._test_logger.log(
                    vacancy.get("name", ""),
                    employer.get("name", ""),
                    test_link or "",
                )
            except OSError as ex:
                logger.error("TestVacancyLoggerPort failed: %s", ex)
        else:
            # Legacy fallback
            try:
                with open(
                    "vacancies_with_tests.txt", "a", encoding="utf-8"
                ) as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(
                        f"[{timestamp}] {vacancy.get('name')} - "
                        f"{employer.get('name')} - {test_link}\n"
                    )
            except OSError as e:
                logger.error(
                    "Не удалось записать вакансию с тестом в файл: %s", e
                )
        self._notify(f"[TEST] ТРЕБУЕТСЯ ТЕСТ (пройдите вручную): {test_link}")
        self._save_skipped_vacancy(
            vacancy, "has_test_manual_required", resume_id
        )

    # ─── Отправка отклика + капча ────────────────────────────────

    def _send_apply_request(
        self, params: dict[str, Any], vacancy: SearchVacancy
    ) -> bool:
        """Отправляет отклик на вакансию с обработкой капчи.

        Returns:
            ``True`` если отклик успешно отправлен.
        """
        if self.command.dry_run:  # type: ignore[union-attr]
            return False
        # VSA wiring (issue #55): если в use case инжектирован адаптер
        # нового ``ApplicationSubmitSlice`` — делегируем отправку ему.
        # Fallback на legacy ``api_client.post`` ниже — для обратной
        # совместимости с конфигурациями, где слайс ещё не подключён.
        if self._application_submit_adapter is not None:
            try:
                return self._application_submit_adapter.apply_one(
                    resume_id=params["resume_id"],
                    vacancy_id=params["vacancy_id"],
                    cover_letter=params.get("message", ""),
                    vacancy=vacancy,
                )
            except ApiError as ex:
                # Адаптер упал с API-ошибкой — не валим рассылку, логируем
                # и идём по legacy-пути (как и было до подключения VSA).
                # Программные ошибки (ValueError, AttributeError, ...) — пусть
                # пропагируются: это баги адаптера, а не runtime-фейлы HH.
                logger.warning(
                    "application_submit adapter failed, falling back to "
                    "legacy api_client.post: %s", ex,
                )
        try:
            res = self.api_client.post(
                "/negotiations",
                params,
                delay=random.uniform(1, 3),
            )
            assert res == {}
            self._notify(
                " [APPLY] Отправили отклик на вакансию",
                vacancy["alternate_url"],
            )
            return True
        except Redirect:
            logger.warning(
                f"Игнорирую перенаправление на форму: "
                f"{vacancy['alternate_url']}"
            )
            return False
        except CaptchaRequired as ex:
            logger.warning(f"Требуется капча: {ex.captcha_url}")
            try:
                success = asyncio.run(self._solve_captcha_async(ex.captcha_url))
                if not success:
                    logger.error("Не удалось решить капчу")
                    raise
                res = self.api_client.post(
                    "/negotiations",
                    params,
                    delay=random.uniform(1, 3),
                )
                assert res == {}
                self._notify(
                    " [APPLY] Отправили отклик на вакансию после капчи",
                    vacancy["alternate_url"],
                )
                return True
            except (
                ApiError,
                BadResponse,
                LimitExceeded,
                AIError,
                AssertionError,
            ) as e:
                logger.error(f"Ошибка при решении капчи: {e}")
                raise
            except (RuntimeError, OSError, asyncio.TimeoutError) as e:
                # "Неожиданные" ошибки инфраструктуры: Playwright crash,
                # сетевой сбой, истечение таймаута. Программные баги
                # (ValueError, TypeError, AttributeError) НЕ ловим — пусть
                # пропагируются как реальные дефекты.
                logger.error(f"Неожиданная ошибка при решении капчи: {e}")
                raise

    async def _solve_captcha_async(self, captcha_url: str) -> bool:
        """Решает капчу через ``CaptchaSolverPort`` (issue #38)
        или legacy Playwright."""
        if self._captcha_solver is not None:
            try:
                captcha_text = await self._captcha_solver.solve_captcha_url(
                    captcha_url
                )
                if captcha_text:
                    logger.info("CaptchaSolverPort solved: %s", captcha_text)
                    return True
                logger.error("CaptchaSolverPort returned empty text")
                return False
            except AIError as ex:
                logger.error("CaptchaSolverPort failed (AI error): %s", ex)
                return False
            except (OSError, asyncio.TimeoutError, RuntimeError) as ex:
                # Инфраструктурные сбои порта (сеть, таймаут, падение
                # Playwright). Программные баги (ValueError, TypeError, ...)
                # НЕ ловим — это дефекты реализации порта.
                logger.error("CaptchaSolverPort failed (unexpected): %s", ex)
                return False

        # Legacy fallback: inline Playwright
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()

                await page.goto(captcha_url, timeout=30000)

                captcha_element = await page.wait_for_selector(
                    self.SEL_CAPTCHA_IMAGE, timeout=10000, state="visible"
                )
                if captcha_element is None:
                    logger.error("Captcha image element not found")
                    return False

                img_bytes = await captcha_element.screenshot()

                captcha_text = await asyncio.to_thread(
                    self.captcha_ai.solve_captcha, img_bytes
                )

                if not captcha_text:
                    logger.error("AI не смог распознать капчу")
                    return False

                logger.info(f"Распознанный текст капчи: {captcha_text}")

                await page.fill(self.SEL_CAPTCHA_INPUT, captcha_text)
                await page.press(self.SEL_CAPTCHA_INPUT, "Enter")

                await page.wait_for_load_state("networkidle", timeout=15000)

                cookies = await context.cookies()
                for c in cookies:
                    self.session.cookies.set(
                        c["name"],
                        c["value"],
                        domain=c.get("domain", ""),
                        path=c.get("path", "/"),
                    )

                return True
            finally:
                await browser.close()

        return False

    # ─── Email ───────────────────────────────────────────────────

    def _maybe_send_email(
        self,
        vacancy: SearchVacancy,
        employer_id: str | None,
        message_placeholders: dict[str, Any],
        site_emails: dict[str, Any],
    ) -> None:
        if not self.command.send_email:  # type: ignore[union-attr]
            return
        mail_to: str | list[str] | None = (
            (vacancy.get("contacts") or {}).get("email")
        )
        if mail_to is None and employer_id is not None:
            mail_to = site_emails.get(employer_id)
        if not mail_to:
            return
        if isinstance(mail_to, list):
            mail_to = ", ".join(mail_to)
        mail_subject = rand_text(
            (self.config.get("apply_mail_subject") if self.config else None)
            or "{Отклик|Резюме} на вакансию %(vacancy_name)s"
        )
        mail_body = unescape_string(
            rand_text(
                (self.config.get("apply_mail_body") if self.config else None)
                or "{Здравствуйте|Добрый день}, "
                "{прошу рассмотреть|пожалуйста рассмотрите} "
                "мое резюме %(resume_url)s на вакансию %(vacancy_name)s."
                % message_placeholders
            )
        )
        try:
            self._send_email(mail_to, mail_subject, mail_body)
            self._notify(
                "[EMAIL] Отправлено письмо на email по поводу вакансии",
                vacancy["alternate_url"],
            )
        except smtplib.SMTPException as ex:
            logger.error(f"Ошибка отправки письма: {ex}")

    def _send_email(self, to: str, subject: str, body: str) -> None:
        """Отправляет email через ``EmailSenderPort`` (issue #36)
        или legacy SMTP клиент."""
        if self._email_sender is not None:
            try:
                self._email_sender.send_email(to, subject, body)
                return
            except smtplib.SMTPException as ex:
                logger.warning("EmailSenderPort failed: %s", ex)

        # Legacy fallback
        if self.smtp is None or self.config is None:
            raise RuntimeError(
                "SMTP клиент или конфиг не настроены "
                "(send_email=True требует обоих)"
            )
        cfg = self.config.get("smtp", {})
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.get("from") or cfg.get("user")
        msg["To"] = to
        msg.set_content(body)
        self.smtp.send_message(msg)

    # ─── Поиск ───────────────────────────────────────────────────

    def _search_params_kwargs(self) -> dict[str, Any]:
        """Собирает kwargs для :func:`build_search_params` из command.

        ``search_params`` — плоский dict, который Operation собрал из
        search-фильтров (``area``, ``metro``, ``schedule``, ...). Сверху
        накладываем ``text`` (из ``command.search``) и ``order_by``
        (из top-level поля command, если не указан в ``search_params``).
        """
        sp = dict(self.command.search_params or {})  # type: ignore[union-attr]
        if self.command.search:  # type: ignore[union-attr]
            sp["text"] = self.command.search
        if self.command.order_by:  # type: ignore[union-attr]
            sp.setdefault("order_by", self.command.order_by)
        return sp

    def _get_search_params(self, page: int) -> dict[str, Any]:
        return build_search_params(
            page=page,
            per_page=self.command.per_page,  # type: ignore[union-attr]
            **self._search_params_kwargs(),
        )

    def _base_search_params(self) -> dict[str, Any]:
        return self._get_search_params(page=0)

    def _get_vacancies(
        self, resume_id: str | None = None
    ):  # -> Iterator[SearchVacancy]
        yield from self.vacancy_search_service.search(
            self._base_search_params(),
            resume_id=resume_id,
        )

    # ─── Excluded filter ─────────────────────────────────────────

    def _is_excluded(self, vacancy: SearchVacancy) -> bool:
        excluded_filter = self.command.excluded_filter
        if not excluded_filter:
            return False

        snippet = vacancy.get("snippet") or {}
        vacancy_summary = " ".join(
            filter(
                None,
                [
                    vacancy.get("name"),
                    snippet.get("requirement"),
                    snippet.get("responsibility"),
                ],
            )
        )

        logger.debug(vacancy_summary)

        excluded_pat: re.Pattern[str] = re.compile(
            excluded_filter, re.IGNORECASE
        )

        if excluded_pat.search(vacancy_summary):
            return True

        # Грузим полный текст вакансии только, если предыдущий
        # фильтр не сработал.
        r = self.session.get("https://hh.ru/vacancy/" + vacancy["id"])
        r.raise_for_status()

        match = re.search(r'"description": (.*)', r.text)
        if match is None:
            return False
        description, _ = self.json_decoder.raw_decode(match.group(1))
        description = strip_tags(description)
        logger.debug(description[:2047])
        return bool(excluded_pat.search(description))
