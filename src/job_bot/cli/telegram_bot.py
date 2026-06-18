"""CLI-операция ``telegram-bot`` (VSA rewrite, issue #147)."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from datetime import time as dtime
from typing import Any, Protocol

from job_bot.shared.health import (
    DEFAULT_HOST,
    DefaultHealthChecks,
    HealthChecks,
    HealthServer,
    TrivialHealthChecks,
)
from job_bot.shared.storage.database import Database

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)

DEFAULT_DIGEST_TIME = "10:00"


class _TelegramBotSlice(Protocol):
    """Minimal slice contract for ``telegram-bot``."""

    @property
    def transport(self) -> Any: ...

    def dispatch_update(self, update: dict[str, Any]) -> None: ...
    def send_digest(self, *, force: bool = False) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``telegram-bot``."""

    once: bool
    send_digest_now: bool
    health_port: int | None
    health_host: str


def _build_health_checks(slice_: _TelegramBotSlice) -> HealthChecks:
    """Build the readiness checks for ``telegram-bot``.

    The slice exposes a :class:`Database` (or anything that quacks
    like one). We pass it straight into :class:`DefaultHealthChecks`;
    the telegram bot doesn't depend on ``api.hh.ru`` so the HH API
    probe is left as ``None`` (only DB is checked).
    """
    db = getattr(slice_, "database", None)
    if not isinstance(db, Database):
        # ``TelegramBotSlice.database`` is a public property; if it's
        # missing or not a ``Database`` (e.g. a test stub), fall back
        # to a trivial check so /ready still answers 200.
        logger.warning(
            "telegram-bot: slice has no Database; "
            "/ready will report 200 unconditionally"
        )
        return TrivialHealthChecks()
    return DefaultHealthChecks(database=db)


class Operation(BaseOperation):
    """Запустить Telegram-бот с long polling."""

    def __init__(self, slice_: _TelegramBotSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help=(
                "Обработать один цикл polling (и одну проверку дайджеста) "
                "и завершиться. Удобно для smoke-тестов и cron."
            ),
        )
        parser.add_argument(
            "--send-digest-now",
            action="store_true",
            help=(
                "Принудительно отправить ежедневный дайджест "
                "через TelegramBotAdapter.send_digest(force=True). "
                "В режиме --once бот завершится сразу после отправки."
            ),
        )
        parser.add_argument(
            "--health-port",
            type=int,
            default=None,
            help=(
                "Запустить HTTP-сервер на указанном порту с "
                "эндпоинтами /health (liveness) и /ready (readiness: "
                "SELECT 1). Используется внешним supervisor-ом. "
                "По умолчанию сервер не запускается."
            ),
        )
        parser.add_argument(
            "--health-host",
            type=str,
            default=DEFAULT_HOST,
            help=(
                "Интерфейс, на котором слушает health-сервер. По "
                "умолчанию: 127.0.0.1 (loopback — безопасно для "
                "локальной разработки). В k8s/Docker указывайте "
                "0.0.0.0, чтобы kubelet/докер-демон мог достучаться "
                "до проб по IP пода/контейнера. Имеет эффект только "
                "вместе с --health-port."
            ),
        )

    def run(self, args: argparse.Namespace) -> int:
        if self._slice is None:
            logger.error("telegram-bot requires a telegram_bot slice")
            return 1
        slice_ = self._slice

        once = bool(getattr(args, "once", False))
        send_digest_now = bool(getattr(args, "send_digest_now", False))

        mode_label = "single-cycle" if once else "long polling"
        logger.info("Telegram bot started (%s)...", mode_label)
        print(f"🤖 Telegram bot started ({mode_label}). Press Ctrl+C to stop.")

        health_server: HealthServer | None = None
        health_port = getattr(args, "health_port", None)
        if health_port is not None:
            health_checks = _build_health_checks(slice_)
            health_server = HealthServer(
                port=health_port,
                checks=health_checks,
                host=getattr(args, "health_host", DEFAULT_HOST),
            )
            try:
                health_server.start()
            except OSError:
                logger.exception(
                    "telegram-bot: failed to bind health port %s", health_port
                )
                return 1

        offset: int | None = None
        digest_done_this_run = False
        try:
            while True:
                try:
                    updates = slice_.transport.get_updates(offset=offset)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Polling error: %s", exc)
                    time.sleep(1.0)
                    if once:
                        if health_server is not None:
                            health_server.stop()
                        return 1
                    continue

                for update in updates:
                    try:
                        slice_.dispatch_update(update)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        logger.exception("Error handling update: %s", update)
                    update_id = update.get("update_id")
                    if update_id is not None:
                        offset = update_id + 1

                if not digest_done_this_run:
                    self._maybe_send_digest(
                        force=send_digest_now, slice_=slice_
                    )
                    digest_done_this_run = True

                if once:
                    logger.info("--once: finished one polling cycle, exiting")
                    print("✅ Single cycle done. Exiting (--once).")
                    if health_server is not None:
                        health_server.stop()
                    return 0
        except KeyboardInterrupt:
            logger.info("Telegram bot is shutting down...")
            print("⛔ Telegram bot stopped.")
            if health_server is not None:
                health_server.stop()
            return 0
        if health_server is not None:
            health_server.stop()
        return 0

    def _maybe_send_digest(
        self,
        *,
        force: bool,
        slice_: _TelegramBotSlice,
        now: datetime | None = None,
    ) -> None:
        """Time-of-day gate + delegated send. Errors are swallowed."""
        if not self._should_send_digest(now=now):
            logger.debug("daily_digest: время ещё не пришло — skip")
            return

        try:
            result = slice_.send_digest(force=force)
        except Exception:  # noqa: BLE001
            logger.exception("daily_digest: непредвиденная ошибка send()")
            return

        sent = getattr(result, "sent", None)
        if sent:
            logger.info(
                "daily_digest: отправлен (drafts=%s, force=%s)",
                getattr(result, "total_drafts", "?"),
                force,
            )
        else:
            logger.info(
                "daily_digest: пропущен (%s, force=%s)",
                getattr(result, "skipped_reason", "no-op"),
                force,
            )

    @staticmethod
    def _should_send_digest(now: datetime | None = None) -> bool:
        """Always send in the VSA-only build (the gate is config-driven)."""
        return True  # legacy gate used telegram.daily_digest_time; we keep it open.

    @staticmethod
    def _parse_digest_time(value: str) -> dtime:
        try:
            parts = value.strip().split(":")
            if len(parts) != 2:
                raise ValueError
            return dtime(int(parts[0]), int(parts[1]))
        except (ValueError, AttributeError):
            hh, mm = DEFAULT_DIGEST_TIME.split(":")
            return dtime(int(hh), int(mm))


__all__ = ("Operation", "Namespace")
