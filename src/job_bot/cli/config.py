"""CLI-операция ``config`` (VSA-rewrite issue #147).

Operations on the on-disk :class:`AppConfig` JSON file. The legacy
free-function helpers (``get_value`` / ``set_value`` / ``del_value`` /
``parse_scalar``) move to
:class:`job_bot.config_auth.handlers.config_kv_handler.ConfigKVHandler`
so other slices can share the same dotted-path logic.

The slice is constructor-injected — the op talks to
:attr:`ConfigAuthSlice.config`, which exposes ``data`` (a dict),
``path`` (the on-disk JSON path), ``kv`` (a
:class:`ConfigKVHandler`-shaped dotted-path helper), and ``save()``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import subprocess
from typing import Any, Protocol

from job_bot.config_auth.handlers.config_kv_handler import ConfigKVHandler

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _ConfigSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def config(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``config``."""

    show_path: bool
    key: str | None
    set: list[str] | None
    edit: bool
    unset: str | None


class Operation(BaseOperation):
    """Операции с конфигурационным файлом.

    По умолчанию выводит содержимое конфига.
    """

    def __init__(self, slice_: _ConfigSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "-e",
            "--edit",
            action="store_true",
            help="Открыть конфигурационный файл в редакторе",
        )
        group.add_argument(
            "-k", "--key", help="Вывести отдельное значение из конфига"
        )
        group.add_argument(
            "-s",
            "--set",
            nargs=2,
            metavar=("KEY", "VALUE"),
            help="Установить значение в конфиг, например, --set openai.model gpt-4o",
        )
        group.add_argument(
            "-u", "--unset", metavar="KEY", help="Удалить ключ из конфига"
        )
        group.add_argument(
            "-p",
            "--show-path",
            "--path",
            action="store_true",
            help="Вывести полный путь к конфигу",
        )

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("config requires a slice with a config port")
            return 1
        config = slice_.config
        # Prefer the port's own ``kv`` helper so a custom slice (e.g. a
        # test fake or a future per-profile variant) can override the
        # dotted-path logic. Fall back to the canonical
        # :class:`ConfigKVHandler` static methods when the port doesn't
        # expose ``kv`` (the default ``ConfigHandler``).
        kv = getattr(config, "kv", ConfigKVHandler)
        data = getattr(config, "data", config)

        if args.set:
            key, raw_value = args.set
            value = kv.parse_scalar(raw_value)
            kv.set_value(data, key, value)
            config.save()
            logger.info("Значение '%s' для ключа '%s' сохранено.", value, key)
            return 0

        if args.unset:
            key = args.unset
            if kv.del_value(data, key):
                config.save()
                logger.info("Ключ '%s' удален из конфига.", key)
            else:
                logger.warning("Ключ '%s' не найден в конфиге.", key)
            return 0

        if args.key:
            value = kv.get_value(data, args.key)
            if value is not None:
                print(value)
            return 0

        config_path = str(
            getattr(config, "path", getattr(config, "_config_path", ""))
        )
        if args.show_path:
            print(config_path)
            return 0

        if args.edit:
            self._open_editor(config_path)
            return 0

        # Default action: show content.
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    def _open_editor(self, filepath: str) -> None:
        """Открывает файл в редакторе по умолчанию в зависимости от ОС."""
        if not filepath:
            logger.error("Не удалось определить путь к конфигу.")
            return
        match platform.system():
            case "Windows":
                os.startfile(filepath)  # type: ignore[attr-defined]
            case "Darwin":  # macOS
                subprocess.run(["open", filepath], check=True)
            case _:  # Linux и остальные
                editor = os.getenv("EDITOR", "xdg-open")
                subprocess.run([editor, filepath], check=True)


__all__ = ("Operation", "Namespace")
