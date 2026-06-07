"""CLI-операция ``prepare-vacancies`` (тонкий адаптер, issue #5).

Парсит argparse-аргументы → строит :class:`PrepareVacanciesCommand` →
получает готовый :class:`PrepareVacanciesUseCase` из :class:`AppContainer`
→ вызывает ``use_case.execute()`` → печатает статистику.

Команда НИКОГДА не отправляет отклики на hh.ru — только готовит черновики
(``application_drafts`` + ``application_test_answers``) для последующего
ревью через Telegram (issue #7-9) и/или apply-worker (issue #10).
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from ..application import PrepareVacanciesCommand
from ..container import AppContainer
from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool


class Namespace(BaseNamespace):
    search_profile: str | None
    dry_run: bool
    per_page: int
    total_pages: int
    force_message: bool
    use_ai: bool
    system_prompt: str


class Operation(BaseOperation):
    """Подготовить черновики откликов (без отправки на hh.ru)."""

    __aliases__ = ("prepare",)

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-p",
            "--search-profile",
            help=(
                "ID конкретного search-профиля из БД. "
                "Если не указан — обрабатываются все включённые профили."
            ),
            default=None,
        )
        parser.add_argument(
            "--per-page",
            help="Сколько вакансий запрашивать на странице поиска.",
            default=100,
            type=int,
        )
        parser.add_argument(
            "--total-pages",
            "--pages",
            help="Верхняя граница числа страниц поиска.",
            default=20,
            type=int,
        )
        parser.add_argument(
            "-f",
            "--force-message",
            "--force",
            help="Всегда генерировать сопроводительное письмо.",
            action=argparse.BooleanOptionalAction,
            default=True,
        )
        parser.add_argument(
            "--use-ai",
            "--ai",
            help=(
                "Использовать AI для генерации сопроводительных писем "
                "и ответов на тесты. По умолчанию письма/тесты строятся "
                "по rule-based fallback."
            ),
            action=argparse.BooleanOptionalAction,
            default=False,
        )
        parser.add_argument(
            "--system-prompt",
            "--ai-system",
            help="System prompt для AI-генерации писем (используется при --use-ai).",
            default=(
                "Ты — опытный специалист, готовящий сопроводительное письмо "
                "для отклика на вакансию. Пиши лаконично, без воды."
            ),
        )
        parser.add_argument(
            "--dry-run",
            help=(
                "Не писать в БД (application_drafts/application_test_answers/"
                "skipped_vacancies/vacancies/employers/resumes). "
                "Только печатать, что было бы подготовлено."
            ),
            action=argparse.BooleanOptionalAction,
            default=False,
        )

    def run(
        self,
        tool: "HHApplicantTool",
        args: Namespace,
    ) -> int:
        cancel_event = getattr(self, "_cancel_event", None)

        command = PrepareVacanciesCommand(
            search_profile=args.search_profile,
            dry_run=args.dry_run,
            per_page=args.per_page,
            total_pages=args.total_pages,
            force_message=args.force_message,
            system_prompt=args.system_prompt,
        )

        use_case = AppContainer(tool).prepare_vacancies_use_case(
            system_prompt=args.system_prompt,
            use_ai=args.use_ai,
        )

        result = use_case.execute(command, cancel_event=cancel_event)

        print(
            f"\n📊 Итог: профилей={result.profiles_processed}, "
            f"увидели={result.vacancies_seen}, "
            f"подготовлено={result.prepared}, "
            f"отклонено AI={result.rejected}, "
            f"пропущено={result.skipped}, "
            f"тест-ответов={result.test_answers}, "
            f"ошибок={result.failed}"
        )
        return 0
