"""CLI-операция ``migrate-db`` (VSA-rewrite issue #147).

Wraps the legacy ``apply_migration`` / ``list_migrations`` helpers from
:mod:`hh_applicant_tool.storage.utils` and lists / applies them.

The slice is constructor-injected. It exposes ``db`` (a
:class:`sqlite3.Connection`) and ``migrations`` (a runner with
``list_migrations()`` / ``apply_migration(name)`` methods).
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _MigrationSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def db(self) -> sqlite3.Connection: ...
    @property
    def migrations(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``migrate-db``."""

    name: str | None


class Operation(BaseOperation):
    """Выполняет миграцию БД.

    Если первым аргументом имя миграции не передано, выведет их список.
    """

    __aliases__ = ("migrate",)

    def __init__(self, slice_: _MigrationSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", nargs="?", help="Имя миграции")

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("migrate-db requires a slice with db + migrations")
            return 1
        db = slice_.db
        migrations = slice_.migrations

        def apply(name: str) -> int:
            migrations.apply_migration(name)
            print("✅ Success!")
            return 0

        try:
            if a := args.name:
                return apply(a)
            if not (migrations_list := migrations.list_migrations()):
                return 0
            if not sys.stdout.isatty():
                print(*migrations_list, sep=os.sep)
                return 0
            print("List of migrations:")
            print()
            for n, migration in enumerate(migrations_list, 1):
                print(f"  [{n}]: {migration}")
            print()
            L = len(migrations_list)
            if n := int(
                input(
                    f"Choose migration [1{f'-{L}' if L > 1 else ''}] "
                    "(Keep empty to exit): "
                )
                or 0
            ):
                return apply(migrations_list[n - 1])
            return 0
        except sqlite3.OperationalError as ex:
            logger.exception(ex)
            logger.warning(
                "Если ничего не помогает, то вы можете просто удалить базу, "
                "сделав бекап:\n\n"
                f"  $ mv {db_path_for(db)}{{.bak}}"
            )
            return 1


def db_path_for(db: sqlite3.Connection) -> str:
    """Best-effort: extract the path from a sqlite3.Connection."""
    try:
        return str(db.execute("PRAGMA database_list").fetchone()[2])
    except Exception:  # noqa: BLE001
        return "<db>"


__all__ = ("Operation", "Namespace")
