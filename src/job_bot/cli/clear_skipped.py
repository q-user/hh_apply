"""CLI-операция ``clear-skipped`` (VSA-rewrite issue #147).

Cleans (or previews) the ``skipped_vacancies`` table. Backed by the
:class:`StorageFacade.skipped_vacancies` legacy repo (15-repo facade
from issue #146).

The storage facade is constructor-injected.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _StorageSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def skipped_vacancies(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``clear-skipped``."""

    reason: str | None
    dry_run: bool


class Operation(BaseOperation):
    """Очистить пропущенные вакансии."""

    __aliases__ = ("clear-skipped-vacancies",)

    def __init__(self, slice_: _StorageSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--reason",
            help=(
                "Очистить только вакансии с указанной причиной "
                "(ai_rejected, excluded_filter, blocked)"
            ),
            type=str,
            default=None,
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Только показать количество записей без удаления",
        )

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error(
                "clear-skipped requires a StorageFacade with "
                "skipped_vacancies repo"
            )
            return 1
        repo = slice_.skipped_vacancies
        reason = args.reason
        dry_run = bool(args.dry_run)

        if reason:
            count = sum(1 for _ in repo.find(reason=reason))
            if dry_run:
                print(f"📋 Найдено {count} записей с причиной '{reason}'")
                return 0
            if count > 0:
                for item in repo.find(reason=reason):
                    repo.delete(item.id, commit=False)
                repo.commit()
                print(f"✂️  Удалено {count} записей с причиной '{reason}'")
            else:
                print(f"❌ Нет записей с причиной '{reason}'")
            return 0

        total = repo.count_total()
        if dry_run:
            print(f"📋 Всего записей в базе: {total}")
            return 0
        if total > 0:
            repo.clear()
            print(f"✂️  Очищено {total} записей из базы пропущенных вакансий")
        else:
            print("📋 База пропущенных вакансий уже пуста")
        return 0


__all__ = ("Operation", "Namespace")
