"""CLI-операция ``max-bot`` (VSA rewrite, issue #147)."""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _MaxBotSlice(Protocol):
    """Minimal slice contract for ``max-bot``."""

    @property
    def transport(self) -> Any: ...
    @property
    def handler(self) -> Any: ...

    def send_message(self, *, chat_id: int, text: str) -> bool: ...


class Namespace(BaseNamespace):
    """Аргументы ``max-bot``."""

    once: bool
    send_message: bool
    chat_id: int | None
    text: str | None


class Operation(BaseOperation):
    """Запустить MAX-бот с long polling."""

    def __init__(self, slice_: _MaxBotSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help=(
                "Обработать один цикл polling и завершиться. "
                "Удобно для smoke-тестов и cron."
            ),
        )
        parser.add_argument(
            "--send-message",
            action="store_true",
            help=(
                "Отправить одно сообщение и выйти (smoke-тест транспорта). "
                "Требует --chat-id и --text."
            ),
        )
        parser.add_argument(
            "--chat-id",
            type=int,
            default=None,
            help="ID чата для --send-message.",
        )
        parser.add_argument(
            "--text",
            type=str,
            default=None,
            help="Текст сообщения для --send-message.",
        )

    def run(self, args: argparse.Namespace) -> int:
        if self._slice is None:
            logger.error("max-bot requires a max_bot slice")
            return 1
        slice_ = self._slice
        once = bool(getattr(args, "once", False))
        send_message = bool(getattr(args, "send_message", False))

        if send_message:
            return self._run_send_message(slice_, args)
        return self._run_polling(slice_, once=once)

    def _run_send_message(self, slice_: Any, args: argparse.Namespace) -> int:
        chat_id = getattr(args, "chat_id", None)
        text = getattr(args, "text", None)
        if chat_id is None or not text:
            logger.error("--send-message требует --chat-id и --text")
            return 1
        ok = slice_.send_message(chat_id=chat_id, text=text)
        if not ok:
            logger.error("MAX API: send_message вернул False")
            return 1
        logger.info("MAX: сообщение отправлено в chat_id=%s", chat_id)
        return 0

    def _run_polling(self, slice_: Any, *, once: bool) -> int:
        mode_label = "single-cycle" if once else "long polling"
        logger.info("MAX bot started (%s)...", mode_label)
        print(f"🤖 MAX bot started ({mode_label}). Press Ctrl+C to stop.")

        try:
            slice_.handler.run(stop_after=1 if once else None)
        except KeyboardInterrupt:
            logger.info("MAX bot is shutting down...")
            print("⛔ MAX bot stopped.")
            return 0
        return 0


__all__ = ("Operation", "Namespace")
