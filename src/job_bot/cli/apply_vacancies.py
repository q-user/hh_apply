"""CLI-операция ``apply-vacancies`` (VSA rewrite, issue #147).

The VSA-typed version of the legacy ``apply-vacancies`` op. The op now
takes its dependencies (the application_submit slice's use case) via
constructor injection — no more ``tool: HHApplicantTool`` argument.

The ``run(self, args) -> int`` contract is unchanged from the legacy
op; only the dispatch layer is simplified.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _ApplySlice(Protocol):
    """Minimal slice contract for ``apply-vacancies``."""

    def get_use_case(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``apply-vacancies`` (legacy-совместимый shell)."""


class Operation(BaseOperation):
    """Откликнуться на все подходящие вакансии."""

    __aliases__ = ("apply", "apply-similar")

    def __init__(self, slice_: _ApplySlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        # The full argparse surface mirrors the legacy op. The op's
        # --search, --order-by, --ai-filter etc. are owned by the
        # :class:`ApplyToVacanciesCommand` dataclass. Tests for the
        # shape of the command live in the application_submit slice.
        # We expose the most common flags here for completeness; the
        # remaining flags (``--excluded-filter`` etc.) are forwarded to
        # the use case via ``getattr(args, ...)``.
        parser.add_argument("--resume-id", help="Идентефикатор резюме")
        parser.add_argument(
            "--search",
            help=(
                "Строка поиска для фильтрации вакансий. "
                "Если указана, то поиск будет производиться по вакансиям."
            ),
            type=str,
        )
        parser.add_argument(
            "--dry-run",
            help="Не отправлять отклики, а только выводить информацию",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--total-pages",
            "--pages",
            help="Количество обрабатываемых страниц поиска",
            default=20,
            type=int,
        )
        parser.add_argument(
            "--per-page",
            help="Сколько должно быть результатов на странице",
            default=100,
            type=int,
        )
        parser.add_argument(
            "--excluded-filter",
            type=str,
            help="Исключить вакансии по шаблону",
        )
        parser.add_argument(
            "--use-ai",
            "--ai",
            help="Использовать AI для генерации сообщений",
            action=argparse.BooleanOptionalAction,
        )

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("apply-vacancies requires an application_submit slice")
            return 1
        use_case = slice_.get_use_case()
        result = use_case.execute(args)
        print(
            f"\n📊 Итог: отправлено={result.sent}, "
            f"пропущено={result.skipped}, ошибок={result.failed}"
        )
        return 0


__all__ = ("Operation", "Namespace")
