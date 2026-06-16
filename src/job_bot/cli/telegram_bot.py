"""CLI-операция ``telegram-bot`` (VSA rewrite, issue #147)."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from datetime import time as dtime
from typing import Any, Protocol

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
                    return 0
        except KeyboardInterrupt:
            logger.info("Telegram bot is shutting down...")
            print("⛔ Telegram bot stopped.")
            return 0
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
