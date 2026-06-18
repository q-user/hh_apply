"""CLI-операция ``max-bot`` (VSA rewrite, issue #147)."""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from job_bot.shared.health import (
    HealthChecks,
    HealthServer,
    TrivialHealthChecks,
)

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
    health_port: int | None


def _build_health_checks(slice_: _MaxBotSlice) -> HealthChecks:
    """Build the readiness checks for ``max-bot``.

    The ``MaxBotSlice`` doesn't expose the SQLite database (the MAX
    transport is the only external dep), so we fall back to a
    trivial check. ``/ready`` then behaves like ``/health`` -- still
    useful as "is the daemon process up?" signal for the supervisor.
    A future issue can add a ``transport.ping()`` probe.
    """
    return TrivialHealthChecks()


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
        parser.add_argument(
            "--health-port",
            type=int,
            default=None,
            help=(
                "Запустить HTTP-сервер на указанном порту с "
                "эндпоинтами /health (liveness) и /ready (readiness). "
                "Используется внешним supervisor-ом. "
                "По умолчанию сервер не запускается."
            ),
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
        return self._run_polling(
            slice_, once=once, health_port=getattr(args, "health_port", None)
        )

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

    def _run_polling(
        self,
        slice_: Any,
        *,
        once: bool,
        health_port: int | None = None,
    ) -> int:
        mode_label = "single-cycle" if once else "long polling"
        logger.info("MAX bot started (%s)...", mode_label)
        print(f"🤖 MAX bot started ({mode_label}). Press Ctrl+C to stop.")

        health_server: HealthServer | None = None
        if health_port is not None:
            health_checks = _build_health_checks(slice_)
            health_server = HealthServer(port=health_port, checks=health_checks)
            try:
                health_server.start()
            except OSError:
                logger.exception(
                    "max-bot: failed to bind health port %s", health_port
                )
                return 1

        try:
            slice_.handler.run(stop_after=1 if once else None)
        except KeyboardInterrupt:
            logger.info("MAX bot is shutting down...")
            print("⛔ MAX bot stopped.")
            if health_server is not None:
                health_server.stop()
            return 0
        if health_server is not None:
            health_server.stop()
        return 0


__all__ = ("Operation", "Namespace")
