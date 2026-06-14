"""Logging setup helpers for the HH applicant tool.

Mirrors the legacy :mod:`hh_applicant_tool.utils.log` module.
Provides a single :func:`setup_logger` function that wires a colored
stream handler (filtered by the user's ``-v`` verbosity) and a
rotating file handler (always at DEBUG level) onto a logger.

The file handler is augmented with a :class:`RedactingFilter` that
masks long hex tokens (HH request IDs, resume IDs, etc.) to keep
sensitive material out of the on-disk log.
"""

from __future__ import annotations

import enum
import logging
import re
from enum import auto
from logging.handlers import RotatingFileHandler
from os import PathLike
from typing import IO, Callable

# 10MB
MAX_LOG_SIZE = 10 << 20


class Color(enum.Enum):
    BLACK = 30
    RED = auto()
    GREEN = auto()
    YELLOW = auto()
    BLUE = auto()
    PURPLE = auto()
    CYAN = auto()
    WHITE = auto()

    def __str__(self) -> str:
        return str(self.value)


class ColorHandler(logging.StreamHandler[IO[str]]):
    _color_map = {
        "CRITICAL": Color.RED,
        "ERROR": Color.RED,
        "WARNING": Color.RED,
        "INFO": Color.GREEN,
        "DEBUG": Color.BLUE,
    }

    def format(self, record: logging.LogRecord) -> str:
        # Подавляем вывод подробного сообщения об ошибке
        orig_exc_info = record.exc_info

        # Детали ошибки показываем только при отладке
        if self.level > logging.DEBUG:
            record.exc_info = None

        message = super().format(record)
        # Обязательно нужно восстановить оригинальное значение или в файловом
        # логе не будет деталей ошибки
        record.exc_info = orig_exc_info
        # isatty = getattr(self.stream, "isatty", None)
        # if isatty and isatty():
        color_code = self._color_map[record.levelname]
        return f"\033[{color_code}m{message}\033[0m"
        # return message


class RedactingFilter(logging.Filter):
    def __init__(
        self,
        patterns: list[str],
        # По умолчанию количество звездочек равно оригинальной строке
        placeholder: str | Callable[[re.Match[str]], str] = lambda m: (
            "*" * len(m.group(0))
        ),
    ):
        super().__init__()
        self.pattern = (
            re.compile(f"({'|'.join(patterns)})") if patterns else None
        )
        self.placeholder = placeholder

    def filter(self, record: logging.LogRecord) -> bool:
        if self.pattern:
            msg = record.getMessage()
            msg = self.pattern.sub(self.placeholder, msg)
            record.msg, record.args = msg, ()

        return True


def setup_logger(
    logger: logging.Logger,
    verbosity_level: int,
    log_file: PathLike[str],
) -> None:
    # В лог-файл пишем все!
    logger.setLevel(logging.DEBUG)
    color_handler = ColorHandler()
    # [C] Critical Error Occurred
    color_handler.setFormatter(
        logging.Formatter("[%(levelname).1s] %(message)s")
    )
    color_handler.setLevel(verbosity_level)

    # Логи
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=MAX_LOG_SIZE,
        # Без ротации файл будет бесконечно расти, а размер не будет ограничваться
        backupCount=1,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    file_handler.setLevel(logging.DEBUG)

    redactor = RedactingFilter(
        [
            r"\b[A-Z0-9]{64,}\b",
            r"\b[a-fA-F0-9]{32,}\b",  # request_id, resume_id
        ]
    )

    file_handler.addFilter(redactor)

    handlers: list[logging.Handler] = [color_handler, file_handler]
    for h in handlers:
        logger.addHandler(h)


__all__ = [
    "Color",
    "ColorHandler",
    "MAX_LOG_SIZE",
    "RedactingFilter",
    "setup_logger",
]
