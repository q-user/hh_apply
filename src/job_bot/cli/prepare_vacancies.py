"""CLI-операция ``prepare-vacancies`` (VSA rewrite, issue #147)."""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _PrepareSlice(Protocol):
    """Minimal slice contract for ``prepare-vacancies``."""

    def get_use_case(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``prepare-vacancies`` (legacy-совместимый shell)."""


class Operation(BaseOperation):
    """Подготовить черновики откликов (без отправки на hh.ru)."""

    __aliases__ = ("prepare",)

    def __init__(self, slice_: _PrepareSlice | None = None) -> None:
        self._slice = slice_

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
            help="Не писать в БД; только печатать, что было бы подготовлено.",
            action=argparse.BooleanOptionalAction,
            default=False,
        )

    def run(self, args: argparse.Namespace) -> int:
        if self._slice is None:
            logger.error("prepare-vacancies requires an application_prep slice")
            return 1
        use_case = self._slice.get_use_case()
        result = use_case.execute(args)
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


__all__ = ("Operation", "Namespace")
