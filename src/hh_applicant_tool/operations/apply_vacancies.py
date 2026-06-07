"""CLI-операция ``apply-vacancies`` (тонкий адаптер).

Парсит argparse-аргументы → строит :class:`ApplyToVacanciesCommand` →
получает готовый :class:`ApplyToVacanciesUseCase` из
:class:`AppContainer` (issue #16) → вызывает ``use_case.execute()`` →
печатает статистику.

Вся бизнес-логика рассылки вынесена в
``hh_applicant_tool.application.use_cases.ApplyToVacanciesUseCase`` (issue
#15). UI ``ui/api.py`` использует тот же ``AppContainer`` напрямую,
без argparse / ``Operation`` (issue #16).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..application import ApplyToVacanciesCommand
from ..container import AppContainer
from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool


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


# Поисковые фильтры, которые собираем в command.search_params.
# ``search`` и ``order_by`` остаются top-level полями DTO.
_SEARCH_PARAM_KEYS: tuple[str, ...] = (
    "schedule",
    "experience",
    "currency",
    "salary",
    "period",
    "date_from",
    "date_to",
    "top_lat",
    "bottom_lat",
    "left_lng",
    "right_lng",
    "sort_point_lat",
    "sort_point_lng",
    "search_field",
    "employment",
    "area",
    "metro",
    "professional_role",
    "industry",
    "employer_id",
    "excluded_employer_id",
    "label",
    "only_with_salary",
    "no_magic",
    "premium",
)


def _build_search_params(args: Namespace) -> dict[str, Any]:
    """Собирает плоский dict search-фильтров из argparse Namespace.

    Пустые значения (``None``, ``[]``, ``False``) отбрасываются —
    :func:`services.build_search_params` оставляет только truthy-поля.
    """
    out: dict[str, Any] = {}
    for key in _SEARCH_PARAM_KEYS:
        value = getattr(args, key, None)
        if value in (None, [], ""):
            continue
        if isinstance(value, bool) and not value:
            continue
        out[key] = value
    return out


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

    def run(self, tool: "HHApplicantTool", args: Namespace) -> None:
        # UI ставит op._cancel_event = cancel_event до вызова run();
        # сохраняем обратную совместимость с этим контрактом.
        cancel_event = getattr(self, "_cancel_event", None)

        command = ApplyToVacanciesCommand(
            resume_id=args.resume_id,
            search=args.search,
            search_params=_build_search_params(args),
            per_page=args.per_page,
            total_pages=args.total_pages,
            dry_run=args.dry_run,
            force_message=args.force_message,
            use_ai=args.use_ai,
            ai_filter=args.ai_filter,
            ai_rate_limit=args.ai_rate_limit,
            skip_tests=args.skip_tests,
            send_email=args.send_email,
            excluded_filter=args.excluded_filter,
            system_prompt=args.system_prompt,
            message_prompt=args.message_prompt,
            letter_file_content=(
                args.letter_file.read_text(encoding="utf-8", errors="ignore")
                if args.letter_file
                else None
            ),
            order_by=args.order_by,
        )

        use_case = AppContainer(tool).apply_to_vacancies_use_case(
            system_prompt=args.system_prompt,
            use_ai=args.use_ai,
            send_email=args.send_email,
        )

        result = use_case.execute(command, cancel_event=cancel_event)

        if result.limit_reached:
            print("⛔ Лимит откликов hh.ru исчерпан. Попробуйте позже.")
        print(f"📝 Отклики на вакансии разосланы! Отправлено: {result.applied}")
