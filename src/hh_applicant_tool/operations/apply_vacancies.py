from __future__ import annotations

import argparse
import asyncio
import html
import logging
import random
import re
from datetime import datetime
from email.message import EmailMessage
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Literal
from urllib.parse import urlparse

import requests

from ..ai.base import AIError
from ..api import BadResponse, Redirect, datatypes
from ..api.datatypes import SearchVacancy
from ..api.errors import ApiError, CaptchaRequired, LimitExceeded
from ..main import BaseNamespace, BaseOperation
from ..services import (
    CoverLetterService,
    RelevanceService,
    VacancySearchService,
    VacancyTestsService,
    build_filter_system_prompt_heavy,
    build_filter_system_prompt_light,
    build_search_params,
    parse_ai_json_response,
)
from ..storage.repositories.errors import RepositoryError
from ..utils.datatypes import VacancyTestsData
from ..utils.json import JSONDecoder
from ..utils.string import rand_text, strip_tags, unescape_string

if TYPE_CHECKING:
    from ..main import HHApplicantTool


logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    resume_id: str | None
    letter_file: Path | None
    ignore_employers: Path | None
    force_message: bool
    use_ai: bool
    ai_filter: Literal["heavy", "light"] | None
    ai_rate_limit: int
    system_prompt: str
    message_prompt: str
    order_by: str
    search: str
    schedule: str
    dry_run: bool
    # Пошли доп фильтры, которых не было
    experience: str
    employment: list[str] | None
    area: list[str] | None
    metro: list[str] | None
    professional_role: list[str] | None
    industry: list[str] | None
    employer_id: list[str] | None
    excluded_employer_id: list[str] | None
    currency: str | None
    salary: int | None
    only_with_salary: bool
    label: list[str] | None
    period: int | None
    date_from: str | None
    date_to: str | None
    top_lat: float | None
    bottom_lat: float | None
    left_lng: float | None
    right_lng: float | None
    sort_point_lat: float | None
    sort_point_lng: float | None
    no_magic: bool
    premium: bool
    per_page: int
    total_pages: int
    excluded_filter: str | None
    max_responses: int
    send_email: bool
    skip_tests: bool


class Operation(BaseOperation):
    """Откликнуться на все подходящие вакансии."""

    __aliases__ = ("apply", "apply-similar")

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--resume-id", help="Идентефикатор резюме")
        parser.add_argument(
            "--search",
            help="Строка поиска для фильтрации вакансий. Если указана, то поиск будет производиться по вакансиям. В остальных случаях отклики будут производиться по списку рекомендованных вакансий.",  # noqa: E501
            type=str,
        )
        parser.add_argument(
            "-L",
            "--letter-file",
            "--letter",
            help="Путь до файла с текстом сопроводительного письма.",
            type=Path,
        )
        parser.add_argument(
            "-f",
            "--force-message",
            "--force",
            help="Всегда отправлять сообщение при отклике",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--use-ai",
            "--ai",
            help="Использовать AI для генерации сообщений",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--ai-filter",
            help="Использовать AI для фильтрации вакансий. Режимы: heavy - полный анализ вакансии и резюме, light - быстрый анализ по названию и навыкам",
            choices=["heavy", "light"],
            default=None,
        )
        parser.add_argument(
            "--ai-rate-limit",
            help="Лимит запросов к AI в минуту для фильтрации",
            type=int,
            default=40,
        )
        parser.add_argument(
            "--system-prompt",
            "--ai-system",
            help="Системный промпт для AI генерации сопроводительных писем",
            default='Ты — опытный специалист, отправляющий персональный отклик на вакансию. \n\nТВОЯ ЛОГИКА:\n1. ТЫ — ЭТО `candidate`. Пиши только от первого лица. Тебе не нужно представляться в начале (твое имя и так привязано к отклику).\n2. ТВОЙ СТИЛЬ: Лаконичный, напористый, экспертный. Без «воды», без заискиваний и без шаблонных фраз («прошу рассмотреть», «буду полезен»). Пиши как профессионал, который ценит свое время и время нанимателя.\n3. ТВОЯ ЗАДАЧА: Продать решение проблемы, описанной в `job.description`, используя факты и метрики из твоего `candidate.experience_summary`.\n\nИНСТРУКЦИИ ПО ТЕКСТУ:\n- Начни сразу с сути: почему ты пишешь и какую конкретную проблему вакансии ты закроешь.\n- Используй только твердые данные (цифры, стек, результаты). Если в резюме написано «сократил на 70%», это должно быть в письме, но без «рекламного» пафоса.\n- НИКАКИХ ПОДПИСЕЙ И ФИНАЛЬНЫХ ФРАЗ: Не пиши «С уважением», не пиши свое имя в конце. Просто закончи предложением о готовности обсудить детали на встрече.\n- НИКАКИХ ПЛЕЙСХОЛДЕРОВ: В тексте не должно быть ничего в скобках [ ], никаких { } и пустых мест. Только готовый к отправке текст.\n\nФОРМАТ ОТВЕТА (JSON):\n{\n  "strategy_note": "Суть мэтча в одно предложение",\n  "cover_letter": "Текст отклика",\n  "resume_focus": "На что давить в интервью"\n}',  # noqa: E501
        )
        parser.add_argument(
            "--message-prompt",
            "--prompt",
            help="Промпт для генерации сопроводительного письма",
            default="Сгенерируй сопроводительное письмо не более 5-7 предложений от моего имени для вакансии",  # noqa: E501
        )
        parser.add_argument(
            "--total-pages",
            "--pages",
            help="Количество обрабатываемых страниц поиска",  # noqa: E501
            default=20,
            type=int,
        )
        parser.add_argument(
            "--per-page",
            help="Сколько должно быть результатов на странице",  # noqa: E501
            default=100,
            type=int,
        )
        parser.add_argument(
            "--send-email",
            help="Отправлять письмо на email компании или рекрутера с просьбой рассмотреть резюме",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--skip-tests",
            help="Пропускать тесты при откликах вместо",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--excluded-filter",
            type=str,
            help=r"Исключить вакансии, если название или описание не соответствует шаблону. Например, `--excluded-filter 'junior|стажир|bitrix|дружн\w+ коллектив|полиграф|open\s*space|опенспейс|хакатон|конкурс|тестов\w+ задан'`",
        )
        parser.add_argument(
            "--max-responses",
            type=int,
            help="Пропускать отклик на вакансии с более чем N откликов (не реализован)",
        )
        parser.add_argument(
            "--dry-run",
            help="Не отправлять отклики, а только выводить информацию",
            action=argparse.BooleanOptionalAction,
        )

        # Дальше идут параметры в точности соответствующие параметрам запроса
        # при поиске подходящих вакансий
        api_search_filters = parser.add_argument_group(
            "Фильтры для поиска вакансий",
            "Эти параметры напрямую соответствуют фильтрам поиска HeadHunter API",
        )

        api_search_filters.add_argument(
            "--order-by",
            help="Сортировка вакансий",
            choices=[
                "publication_time",
                "salary_desc",
                "salary_asc",
                "relevance",
                "distance",
            ],
            # default="relevance",
        )
        api_search_filters.add_argument(
            "--experience",
            help="Уровень опыта работы (noExperience, between1And3, between3And6, moreThan6)",
            type=str,
            default=None,
        )
        api_search_filters.add_argument(
            "--schedule",
            help="Тип графика (fullDay, shift, flexible, remote, flyInFlyOut)",
            type=str,
        )
        api_search_filters.add_argument(
            "--employment", nargs="+", help="Тип занятости"
        )
        api_search_filters.add_argument(
            "--area", nargs="+", help="Регион (area id)"
        )
        api_search_filters.add_argument(
            "--metro", nargs="+", help="Станции метро (metro id)"
        )
        api_search_filters.add_argument(
            "--professional-role", nargs="+", help="Проф. роль (id)"
        )
        api_search_filters.add_argument(
            "--industry", nargs="+", help="Индустрия (industry id)"
        )
        api_search_filters.add_argument(
            "--employer-id", nargs="+", help="ID работодателей"
        )
        api_search_filters.add_argument(
            "--excluded-employer-id", nargs="+", help="Исключить работодателей"
        )
        api_search_filters.add_argument(
            "--currency", help="Код валюты (RUR, USD, EUR)"
        )
        api_search_filters.add_argument(
            "--salary", type=int, help="Минимальная зарплата"
        )
        api_search_filters.add_argument(
            "--only-with-salary",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        api_search_filters.add_argument(
            "--label", nargs="+", help="Метки вакансий (label)"
        )
        api_search_filters.add_argument(
            "--period", type=int, help="Искать вакансии за N дней"
        )
        api_search_filters.add_argument(
            "--date-from", help="Дата публикации с (YYYY-MM-DD)"
        )
        api_search_filters.add_argument(
            "--date-to", help="Дата публикации по (YYYY-MM-DD)"
        )
        api_search_filters.add_argument(
            "--top-lat", type=float, help="Гео: верхняя широта"
        )
        api_search_filters.add_argument(
            "--bottom-lat", type=float, help="Гео: нижняя широта"
        )
        api_search_filters.add_argument(
            "--left-lng", type=float, help="Гео: левая долгота"
        )
        api_search_filters.add_argument(
            "--right-lng", type=float, help="Гео: правая долгота"
        )
        api_search_filters.add_argument(
            "--sort-point-lat",
            type=float,
            help="Координата lat для сортировки по расстоянию",
        )
        api_search_filters.add_argument(
            "--sort-point-lng",
            type=float,
            help="Координата lng для сортировки по расстоянию",
        )
        api_search_filters.add_argument(
            "--no-magic",
            action="store_true",
            help="Отключить авторазбор текста запроса",
        )
        api_search_filters.add_argument(
            "--premium",
            default=False,
            action=argparse.BooleanOptionalAction,
            help="Только премиум вакансии",
        )
        api_search_filters.add_argument(
            "--search-field",
            nargs="+",
            help="Поля поиска (name, company_name и т.п.)",
        )

    cover_letter: str = "{Здравствуйте|Добрый день}, меня зовут %(first_name)s. {Прошу|Предлагаю} рассмотреть {мою кандидатуру|мое резюме «%(resume_title)s»} на вакансию «%(vacancy_name)s». С уважением, %(first_name)s."

    @property
    def api_client(self):
        return self.tool.api_client

    @property
    def args(self) -> Namespace:
        return self._args

    def run(
        self,
        tool: HHApplicantTool,
        args: Namespace,
    ) -> None:
        self.tool = tool
        self._args = args
        self.cover_letter = (
            args.letter_file.read_text(encoding="utf-8", errors="ignore")
            if args.letter_file
            else self.cover_letter
        )
        self.area = args.area
        self.bottom_lat = args.bottom_lat
        self.currency = args.currency
        self.date_from = args.date_from
        self.date_to = args.date_to
        self.dry_run = args.dry_run
        self.employer_id = args.employer_id
        self.employment = args.employment
        self.excluded_employer_id = args.excluded_employer_id
        self.excluded_filter = args.excluded_filter
        self.experience = args.experience
        self.force_message = args.force_message
        self.industry = args.industry
        self.label = args.label
        self.left_lng = args.left_lng
        self.max_responses = args.max_responses
        self.metro = args.metro
        self.no_magic = args.no_magic
        self.only_with_salary = args.only_with_salary
        self.order_by = args.order_by
        self.per_page = args.per_page
        self.period = args.period
        self.message_prompt = args.message_prompt
        self.premium = args.premium
        self.professional_role = args.professional_role
        self.resume_id = args.resume_id
        self.right_lng = args.right_lng
        self.salary = args.salary
        self.schedule = args.schedule
        self.search = args.search
        self.search_field = args.search_field
        self.sort_point_lat = args.sort_point_lat
        self.sort_point_lng = args.sort_point_lng
        self.top_lat = args.top_lat
        self.total_pages = args.total_pages
        self.cover_letter_ai = (
            tool.get_cover_letter_ai(args.system_prompt)
            if args.use_ai
            else None
        )
        self.ai_filter = args.ai_filter
        self.relevance_service = RelevanceService(
            self.api_client, ai_client=None
        )
        self.cover_letter_service = CoverLetterService(
            self.api_client,
            self.cover_letter_ai,
            template=self.cover_letter,
        )
        self.vacancy_tests_service = VacancyTestsService(
            self.tool.session,
            self.cover_letter_ai,
        )
        self.vacancy_search_service = VacancySearchService(
            self.api_client,
            per_page=self.per_page,
            total_pages=self.total_pages,
        )
        self.vacancy_filter_ai = None

        self._apply_vacancies()

    def _get_full_resume(self, resume_id: str) -> dict:
        return self.api_client.get(f"/resumes/{resume_id}")

    def _analyze_resume_heavy(self, resume: dict) -> str:
        return self.relevance_service.analyze_resume_heavy(resume)

    def _analyze_resume_light(self, resume: dict) -> str:
        return self.relevance_service.analyze_resume_light(resume)

    def _get_vacancy_key_skills(self, vacancy_id: str | int) -> str:
        return self.relevance_service.get_vacancy_key_skills(vacancy_id)

    def _build_vacancy_context(
        self,
        vacancy: dict,
        full_vacancy: dict | None = None,
        include_full: bool = False,
    ) -> str:
        return self.relevance_service.build_vacancy_context(
            vacancy,
            full_vacancy=full_vacancy,
            include_full=include_full,
        )

    def _ask_ai_suitability(
        self, prompt: str, vacancy_name: str, log_suffix: str = ""
    ) -> bool:
        return self.relevance_service._ask_ai_suitability(
            prompt,
            vacancy_name,
            log_suffix,
        ).suitable

    def _parse_ai_json_response(self, response: str) -> bool | None:
        result = parse_ai_json_response(response)
        return None if result is None else result.suitable

    def _is_vacancy_suitable_heavy(self, vacancy: dict) -> bool:
        return self.relevance_service.is_suitable_heavy(vacancy).suitable

    def _is_vacancy_suitable_light(self, vacancy: dict) -> bool:
        return self.relevance_service.is_suitable_light(vacancy).suitable

    def _build_filter_system_prompt_heavy(self, resume_analysis: str) -> str:
        return build_filter_system_prompt_heavy(resume_analysis)

    def _build_filter_system_prompt_light(self, resume_analysis: str) -> str:
        return build_filter_system_prompt_light(resume_analysis)

    SEL_CAPTCHA_IMAGE = 'img[data-qa="account-captcha-picture"]'
    SEL_CAPTCHA_INPUT = 'input[data-qa="account-captcha-input"]'

    # Даже куки не грузятся, исправь
    async def _solve_captcha_async(self, captcha_url: str) -> bool:
        from playwright.async_api import async_playwright

        captcha_ai = self.tool.get_captcha_ai()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()

                await page.goto(captcha_url, timeout=30000)

                captcha_element = await page.wait_for_selector(
                    self.SEL_CAPTCHA_IMAGE, timeout=10000, state="visible"
                )

                img_bytes = await captcha_element.screenshot()

                captcha_text = await asyncio.to_thread(
                    captcha_ai.solve_captcha, img_bytes
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
                    self.tool.session.cookies.set(
                        c["name"],
                        c["value"],
                        domain=c.get("domain", ""),
                        path=c.get("path", "/"),
                    )

                return True
            finally:
                await browser.close()

        return False

    def _apply_vacancies(self) -> None:
        resumes: list[datatypes.Resume] = self.tool.get_resumes()
        try:
            self.tool.storage.resumes.save_batch(resumes)
        except RepositoryError as ex:
            logger.exception(ex)
        resumes = (
            list(filter(lambda x: x["id"] == self.resume_id, resumes))
            if self.resume_id
            else resumes
        )
        # Выбираем только опубликованные
        resumes = list(
            filter(lambda x: x["status"]["id"] == "published", resumes)
        )
        if not resumes:
            logger.warning("У вас нет опубликованных резюме")
            return

        me: datatypes.User = self.tool.get_me()
        seen_employers = set()

        for resume in resumes:
            limit_reached = self._apply_resume(
                resume=resume,
                user=me,
                seen_employers=seen_employers,
            )
            if limit_reached:
                logger.warning(
                    "Лимит откликов hh.ru исчерпан. Пропускаю оставшиеся резюме."
                )
                print("⛔ Лимит откликов hh.ru исчерпан. Попробуйте позже.")
                break

        # Синхронизация откликов
        # for neg in self.tool.get_negotiations():
        #     try:
        #         self.tool.storage.negotiations.save(neg)
        #     except RepositoryError as e:
        #         logger.warning(e)

        print("📝 Отклики на вакансии разосланы!")

    def _apply_resume(
        self,
        resume: datatypes.Resume,
        user: datatypes.User,
        seen_employers: set[str],
    ) -> bool:
        """Оркестратор рассылки откликов: цикл по вакансиям + делегирование."""
        logger.info(
            "Начинаю рассылку откликов для резюме: %s (%s)",
            resume["alternate_url"],
            resume["title"],
        )
        print("[START] Начинаю рассылку откликов для резюме:", resume["title"])

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
        storage = self.tool.storage
        site_emails: dict = {}
        resume_analysis = self._init_ai_filter(resume)

        for vacancy in self._get_vacancies(resume_id=resume["id"]):
            if (
                getattr(self, "_cancel_event", None)
                and self._cancel_event.is_set()
            ):
                logger.info("Операция отменена пользователем")
                break
            try:
                if self._check_vacancy_skips(vacancy, resume, do_apply):
                    continue

                self._save_vacancy_to_storage(vacancy, storage)

                self._load_employer_profile(
                    vacancy, seen_employers, storage, site_emails
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
        print(
            f"[DONE] Закончили рассылку для резюме: {resume['title']}. "
            f"Отправлено: {applied_count}"
        )
        return limit_reached

    # ─────────────────────────────────────────────
    # Helper'ы _apply_resume (распил 408-строчной функции)
    # ─────────────────────────────────────────────

    def _init_ai_filter(self, resume: datatypes.Resume) -> str:
        """Инициализирует AI-фильтр вакансий (heavy/light).

        Устанавливает self.vacancy_filter_ai.
        Возвращает текст анализа резюме (resume_analysis).
        """
        if not self.ai_filter:
            return ""
        if self.ai_filter == "heavy":
            resume_analysis = self.relevance_service.analyze_resume_heavy(
                resume
            )
            system_prompt = build_filter_system_prompt_heavy(resume_analysis)
        elif self.ai_filter == "light":
            resume_analysis = self.relevance_service.analyze_resume_light(
                resume
            )
            system_prompt = build_filter_system_prompt_light(resume_analysis)
        else:
            raise ValueError(f"Неизвестный режим AI фильтра: {self.ai_filter}")
        logger.debug(
            "AI системный промпт (%s): %s", self.ai_filter, system_prompt
        )
        self.vacancy_filter_ai = self.tool.get_vacancy_filter_ai(system_prompt)
        if self.args.ai_rate_limit:
            self.vacancy_filter_ai.rate_limit = self.args.ai_rate_limit
        self.relevance_service.ai_client = self.vacancy_filter_ai
        return resume_analysis

    def _save_vacancy_to_storage(self, vacancy: SearchVacancy, storage) -> None:
        """Сохраняет вакансию и её контакты в локальное хранилище."""
        try:
            storage.vacancies.save(vacancy)
        except RepositoryError as ex:
            logger.debug(ex)
        if vacancy.get("contacts"):
            logger.debug(
                f"Найдены контакты в вакансии: {vacancy['alternate_url']}"
            )
            try:
                storage.vacancy_contacts.save(vacancy)
            except RepositoryError as ex:
                logger.exception(ex)

    def _check_vacancy_skips(
        self,
        vacancy: SearchVacancy,
        resume: datatypes.Resume,
        do_apply: bool,
    ) -> str | None:
        """Проверяет все условия пропуска вакансии.

        Возвращает строку-причину (для логов) или None если вакансия ОК.
        Содержит побочные эффекты: save_skipped, blacklist, print/log.
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
                print("⛔ Пришел отказ от", vacancy["alternate_url"])
            return "already_responded"
        if vacancy.get("archived"):
            logger.debug(
                "Пропускаем вакансию в архиве: %s",
                vacancy["alternate_url"],
            )
            return "archived"
        if vacancy.get("has_test") and self.args.skip_tests:
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
                "Вакансия попала под фильтр: %s", vacancy["alternate_url"]
            )
            self._save_skipped_vacancy(vacancy, "excluded_filter", resume["id"])
            self.api_client.put(f"/vacancies/blacklisted/{vacancy['id']}")
            logger.info(
                "Вакансия добавлена в черный список: %s",
                vacancy["alternate_url"],
            )
            return "excluded"
        # AI фильтрация
        if self.ai_filter and self.vacancy_filter_ai:
            if self._is_vacancy_already_skipped(vacancy, resume["id"]):
                logger.debug(
                    "Вакансия уже была отклонена ранее: %s",
                    vacancy["alternate_url"],
                )
                print(
                    ">> Вакансия уже отклонена ранее",
                    vacancy["alternate_url"],
                )
                return "ai_already_skipped"
            if self.ai_filter == "heavy":
                is_suitable = self._is_vacancy_suitable_heavy(vacancy)
            elif self.ai_filter == "light":
                is_suitable = self._is_vacancy_suitable_light(vacancy)
            else:
                raise ValueError(
                    f"Неизвестный режим AI фильтра: {self.ai_filter}"
                )
            if not is_suitable:
                logger.info(
                    "Вакансия отклонена AI фильтром (%s): %s",
                    self.ai_filter,
                    vacancy["alternate_url"],
                )
                print(
                    f"[AI] ({self.ai_filter}) посчитал неподходящей",
                    vacancy["alternate_url"],
                )
                self._save_skipped_vacancy(vacancy, "ai_rejected", resume["id"])
                return "ai_rejected"
        return None

    def _load_employer_profile(
        self,
        vacancy: SearchVacancy,
        seen_employers: set[str],
        storage,
        site_emails: dict,
    ) -> None:
        """Загружает профиль работодателя и парсит его сайт на email'ы.

        Мутирует site_emails[employer_id] в случае успешного парсинга.
        """
        employer = vacancy.get("employer", {})
        employer_id = employer.get("id")
        if not employer_id or employer_id in seen_employers:
            return
        employer_profile: datatypes.Employer = self.api_client.get(
            f"/employers/{employer_id}"
        )
        try:
            storage.employers.save(employer_profile)
        except RepositoryError as ex:
            logger.exception(ex)
        if not (
            self.args.send_email
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
                storage.employer_sites.save(
                    {
                        "site_url": site_url,
                        "employer_id": employer_id,
                        "subdomains": [],
                        **site_info,
                    }
                )
            except RepositoryError as ex:
                logger.exception(ex)

    @staticmethod
    def _build_message_placeholders(
        vacancy: SearchVacancy, placeholders: dict
    ) -> dict:
        employer = vacancy.get("employer", {})
        return {
            "vacancy_name": vacancy.get("name", ""),
            "employer_name": employer.get("name", ""),
            **placeholders,
        }

    def _generate_cover_letter(
        self,
        vacancy: SearchVacancy,
        message_placeholders: dict,
        resume_analysis: str,
        resume: datatypes.Resume,
    ) -> str:
        """Генерирует сопроводительное письмо (AI или шаблон)."""
        return self.cover_letter_service.generate(
            vacancy,
            message_placeholders,
            resume_analysis=resume_analysis,
            resume=resume,
            force=self.force_message,
            required_by_vacancy=bool(vacancy.get("response_letter_required")),
        )

    def _handle_vacancy_test(
        self, vacancy: SearchVacancy, resume_id: str
    ) -> None:
        """Обрабатывает вакансию с тестом: логирует, сохраняет skipped."""
        test_link = vacancy.get("alternate_url")
        employer = vacancy.get("employer", {})
        logger.info("Найдена вакансия с тестом: %s", test_link)
        try:
            with open("vacancies_with_tests.txt", "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(
                    f"[{timestamp}] {vacancy.get('name')} - "
                    f"{employer.get('name')} - {test_link}\n"
                )
        except Exception as e:
            logger.error(f"Не удалось записать вакансию с тестом в файл: {e}")
        print(f"[TEST] ТРЕБУЕТСЯ ТЕСТ (пройдите вручную): {test_link}")
        self._save_skipped_vacancy(
            vacancy, "has_test_manual_required", resume_id
        )

    def _send_apply_request(self, params: dict, vacancy: SearchVacancy) -> bool:
        """Отправляет отклик на вакансию с обработкой капчи.

        Возвращает True если отклик успешно отправлен.
        """
        if self.dry_run:
            return False
        try:
            res = self.api_client.post(
                "/negotiations",
                params,
                delay=random.uniform(1, 3),
            )
            assert res == {}
            print(
                " [APPLY] Отправили отклик на вакансию",
                vacancy["alternate_url"],
            )
            return True
        except Redirect:
            logger.warning(
                f"Игнорирую перенаправление на форму: "
                f"{vacancy['alternate_url']}"  # noqa: E501
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
                print(
                    " [APPLY] Отправили отклик на вакансию после капчи",
                    vacancy["alternate_url"],
                )
                return True
            except Exception as e:
                logger.error(f"Ошибка при решении капчи: {e}")
                raise

    def _maybe_send_email(
        self,
        vacancy: SearchVacancy,
        employer_id: str | None,
        message_placeholders: dict,
        site_emails: dict,
    ) -> None:
        """Отправляет сопроводительное письмо на email работодателя."""
        if not self.args.send_email:
            return
        mail_to: str | list[str] | None = (
            (vacancy.get("contacts") or {}).get("email")
        ) or site_emails.get(employer_id)
        if not mail_to:
            return
        if isinstance(mail_to, list):
            mail_to = ", ".join(mail_to)
        mail_subject = rand_text(
            self.tool.config.get("apply_mail_subject")
            or "{Отклик|Резюме} на вакансию %(vacancy_name)s"
        )
        mail_body = unescape_string(
            rand_text(
                self.tool.config.get("apply_mail_body")
                or "{Здравствуйте|Добрый день}, "
                "{прошу рассмотреть|пожалуйста рассмотрите} "
                "мое резюме %(resume_url)s на вакансию %(vacancy_name)s."
                % message_placeholders
            )
        )
        try:
            self._send_email(mail_to, mail_subject, mail_body)
            print(
                "[EMAIL] Отправлено письмо на email по поводу вакансии",
                vacancy["alternate_url"],
            )
        except Exception as ex:
            logger.error(f"Ошибка отправки письма: {ex}")

    def _send_email(self, to: str, subject: str, body: str) -> None:
        cfg = self.tool.config.get("smtp", {})
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.get("from") or cfg.get("user")
        msg["To"] = to
        msg.set_content(body)
        self.tool.smtp.send_message(msg)

    json_decoder = JSONDecoder()

    def _get_vacancy_tests(self, response_url: str) -> VacancyTestsData:
        """Парсит тесты"""
        return self.vacancy_tests_service.fetch_tests(response_url)

    def _solve_vacancy_test(
        self,
        vacancy_id: str | int,
        resume_hash: str,
        letter: str = "",
    ) -> dict[str, Any]:
        """Загружает тест, подготавливает ответы и отправляет отклик."""
        response_url = f"https://hh.ru/applicant/vacancy_response?vacancyId={vacancy_id}&startedWithQuestion=false&hhtmFrom=vacancy"

        tests_data = self.vacancy_tests_service.fetch_tests(response_url)

        try:
            test_data = tests_data[str(vacancy_id)]
        except KeyError as ex:
            raise ValueError("Отсутствуют данные теста для вакансии.") from ex

        answers = self.vacancy_tests_service.prepare_answers(test_data)
        payload = self.vacancy_tests_service.build_apply_payload_from_answers(
            test_data,
            answers,
            vacancy_id=vacancy_id,
            resume_hash=resume_hash,
            letter=letter,
            xsrf_token=self.tool.xsrf_token,
        )
        return self.vacancy_tests_service.submit_apply(
            response_url,
            payload,
            xsrf_token=self.tool.xsrf_token,
        )

    def _parse_site(self, url: str) -> dict[str, Any]:
        with self.tool.session.get(url, timeout=10) as r:
            val = lambda m: html.unescape(m.group(1)) if m else ""

            title = val(re.search(r"<title>(.*?)</title>", r.text, re.I | re.S))
            description = val(
                re.search(
                    r'<meta name="description" content="(.*?)"', r.text, re.I
                )
            )
            generator = val(
                re.search(
                    r'<meta name="generator" content="(.*?)"', r.text, re.I
                )
            )

            # Поиск email
            emails = set(
                m.group(0)
                # Исключение всякого мусора типа energy-software-slider-225x225@2x.png
                for m in re.finditer(
                    r"\b[a-z][a-z0-9_.-]+@([a-z0-9][a-z0-9-]+)(?!\.(?:png|jpe?g|bmp|gif|ico|js|css)\b)(\.[a-z0-9][a-z0-9-]+)+\b",
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
                # Не работает, если отключена проверка сертификата
                "ip_address": r.raw._connection.sock.getpeername()[0]
                if r.raw._connection
                else None,
            }

    # Слишком тормознутая... Толи российские айпи заблокированы
    def _get_subdomains(self, url: str) -> set[str]:
        domain = urlparse(url).netloc
        r = self.tool.session.get(
            "https://crt.sh",
            params={"q": domain, "output": "json"},
            timeout=30,
        )

        r.raise_for_status()

        return set(
            item
            for item in chain.from_iterable(
                item["name_value"].split() for item in r.json()
            )
            if not item.startswith("*.")
        )

    def _search_params_kwargs(self) -> dict[str, Any]:
        return {
            "order_by": self.order_by,
            "text": self.search,
            "schedule": self.schedule,
            "experience": self.experience,
            "currency": self.currency,
            "salary": self.salary,
            "period": self.period,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "top_lat": self.top_lat,
            "bottom_lat": self.bottom_lat,
            "left_lng": self.left_lng,
            "right_lng": self.right_lng,
            "sort_point_lat": self.sort_point_lat,
            "sort_point_lng": self.sort_point_lng,
            "search_field": self.search_field,
            "employment": self.employment,
            "area": self.area,
            "metro": self.metro,
            "professional_role": self.professional_role,
            "industry": self.industry,
            "employer_id": self.employer_id,
            "excluded_employer_id": self.excluded_employer_id,
            "label": self.label,
            "only_with_salary": self.only_with_salary,
            "no_magic": self.no_magic,
            "premium": self.premium,
        }

    def _get_search_params(self, page: int) -> dict[str, Any]:
        return build_search_params(
            page=page,
            per_page=self.per_page,
            **self._search_params_kwargs(),
        )

    def _base_search_params(self) -> dict[str, Any]:
        return self._get_search_params(page=0)

    def _get_vacancies(
        self, resume_id: str | None = None
    ) -> Iterator[SearchVacancy]:
        yield from self.vacancy_search_service.search(
            self._base_search_params(),
            resume_id=resume_id,
        )

    def _is_excluded(self, vacancy: SearchVacancy) -> bool:
        if not self.excluded_filter:
            return False

        snippet = vacancy.get("snippet", {})
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

        excluded_pat: re.Pattern = re.compile(
            self.excluded_filter, re.IGNORECASE
        )

        if excluded_pat.search(vacancy_summary):
            return True

        # Грузим полный текст вакансии только, если предыдущий фильтр не сработал
        r = self.tool.session.get("https://hh.ru/vacancy/" + vacancy["id"])
        r.raise_for_status()

        description, _ = self.json_decoder.raw_decode(
            re.search(r'"description": (.*)', r.text).group(1)
        )
        description = strip_tags(description)
        logger.debug(description[:2047])
        return bool(excluded_pat.search(description))

    def _is_vacancy_already_skipped(
        self, vacancy: SearchVacancy, resume_id: str | None = None
    ) -> bool:
        try:
            vacancy_id = vacancy["id"]

            if resume_id:
                if any(
                    self.tool.storage.skipped_vacancies.find(
                        resume_id=resume_id,
                        vacancy_id=vacancy_id,
                    )
                ):
                    return True

            return any(
                self.tool.storage.skipped_vacancies.find(
                    resume_id="",
                    vacancy_id=vacancy_id,
                )
            )

        except Exception:
            return False

    def _save_skipped_vacancy(
        self, vacancy: SearchVacancy, reason: str, resume_id: str | None = None
    ) -> None:
        try:
            employer = vacancy.get("employer", {})
            self.tool.storage.skipped_vacancies.save(
                {
                    "resume_id": resume_id or "",
                    "vacancy_id": vacancy["id"],
                    "reason": reason,
                    "alternate_url": vacancy.get("alternate_url"),
                    "name": vacancy.get("name"),
                    "employer_name": employer.get("name"),
                    "created_at": datetime.now(),
                }
            )
        except Exception as ex:
            logger.warning(f"Не удалось сохранить пропущенную вакансию: {ex}")
