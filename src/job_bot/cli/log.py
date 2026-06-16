"""CLI-операция ``log`` (VSA-rewrite issue #147).

Opens the application's log file (``LOG_FILENAME`` from
:mod:`job_bot.shared.config.settings`) in the configured pager.

The settings (which expose the log path) are constructor-injected.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _SettingsSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def settings(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``log``."""

    follow: bool


class Operation(BaseOperation):
    """Просмотр файла-лога."""

    def __init__(self, slice_: _SettingsSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-f",
            "--follow",
            action="store_true",
            help="Следить за файлом (режим follow, аналог less +F)",
        )

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("log requires a slice with settings")
            return 1
        log_path = str(slice_.settings.log_path)

        if not os.path.exists(log_path):
            logger.error("Файл лога не найден: %s", log_path)
            return 1

        if sys.platform == "win32":
            os.startfile(log_path)  # type: ignore[attr-defined]
            return 0

        pager = os.getenv("PAGER", "less")
        if not shutil.which(pager):
            logger.error("Не найден просмотрщик '%s'", pager)
            if pager == "less":
                logger.error(
                    "Попробуйте установить less: "
                    '"sudo apt install less" или "sudo yum install less"'
                )
            return 1

        cmd = [pager]

        if pager == "less":
            # -R позволяет отображать цвета (ANSI codes)
            # -S отключает перенос строк (удобно для логов)
            cmd.extend(["-R", "-S"])
            if args.follow:
                cmd.append("+F")

        cmd.append(log_path)

        try:
            subprocess.run(cmd, check=False)
        except FileNotFoundError:
            logger.error("Не удалось запустить просмотрщик '%s'", pager)
            return 1
        except KeyboardInterrupt:
            pass
        return 0


__all__ = ("Operation", "Namespace")
