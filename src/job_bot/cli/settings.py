"""CLI-операция ``settings`` (VSA-rewrite issue #147).

Thin VSA adapter over the :class:`StorageFacade.settings` legacy repo.
Supports ``key VALUE`` to set, ``key`` to get, ``--delete`` to delete
(specific key or all), and no-arg to list.

The storage facade is constructor-injected.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Protocol

from prettytable import PrettyTable

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


# Sentinel value for "argument not passed". Python's ``argparse`` has no
# native way to distinguish "not passed" from "passed as ``None``" for
# optional positionals — so we use a unique sentinel instance.
class _Missing:
    def __repr__(self) -> str:
        return "MISSING"

    def __str__(self) -> str:
        return "Не установлено"


MISSING: Any = _Missing()


class _StorageSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def settings(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``settings``."""

    key: Any
    value: Any
    delete: bool


class Operation(BaseOperation):
    """Просмотр и управление настройками."""

    __aliases__ = ("setting",)

    def __init__(self, slice_: _StorageSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-d",
            "--delete",
            action="store_true",
            help=(
                "Удалить настройку по ключу либо удалить все настройки, "
                "если ключ не передан"
            ),
        )
        parser.add_argument(
            "key", nargs="?", help="Ключ настройки", default=MISSING
        )
        parser.add_argument(
            "value",
            nargs="?",
            type=_parse_value,
            help="Значение настройки",
            default=MISSING,
        )

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error(
                "settings requires a StorageFacade with a settings repo"
            )
            return 1
        settings = slice_.settings

        if args.delete:
            if args.key is not MISSING:
                settings.delete_value(args.key)
                print(f"🗑️ Настройка '{args.key}' удалена")
            else:
                settings.clear()
            return 0

        if args.key is not MISSING and args.value is not MISSING:
            settings.set_value(args.key, args.value)
            print(f"✅ Установлено значение для '{args.key}'")
            return 0

        if args.key is not MISSING:
            # Get value
            value = settings.get_value(args.key, MISSING)
            if value is not MISSING:
                print(value)
            else:
                print(f"⚠️ Настройка '{args.key}' не найдена")
            return 0

        # List all settings
        all_settings = settings.find()
        t = PrettyTable(field_names=["Ключ", "Тип", "Значение"], align="l")
        for setting in all_settings:
            if setting.key.startswith("_"):
                continue
            t.add_row(
                [
                    setting.key,
                    type(setting.value).__name__,
                    setting.value,
                ]
            )
        print(t)
        return 0


def _parse_value(v: str) -> Any:
    """Best-effort: try JSON-decoding the value, fall back to str."""
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


__all__ = ("Operation", "Namespace", "MISSING")
