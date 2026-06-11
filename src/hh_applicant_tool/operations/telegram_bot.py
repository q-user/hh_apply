"""CLI-операция ``telegram-bot`` (issue #7; VSA switchover issue #56).

Запускает Telegram-бот в режиме long polling. Опрос, маршрутизация
команд, review/digest-флоу — всё делегировано VSA-слайсу
:class:`job_bot.telegram_bot.slice.TelegramBotSlice` через тонкий
адаптер :class:`TelegramBotAdapter`. Операция владеет только polling
loop'ом, time-of-day гейтом для ежедневного дайджеста и graceful
shutdown по Ctrl+C.

CLI-флаги:
  * ``--once`` — обработать один цикл polling и завершиться.
  * ``--send-digest-now`` — принудительно отправить дайджест
    (``force=True`` в :meth:`TelegramBotAdapter.send_digest`).

Wiring (issue #56):
  * ``Operation(bot_adapter=...)`` — DI-инжекция адаптера из тестов /
    из :meth:`AppContainer.create_telegram_bot_adapter`. Если
    ``bot_adapter is None`` — операция не строит адаптер сама;
    CLI запускает её через :class:`AppContainer` (которая всегда
    передаёт готовый адаптер), а unit-тесты могут замокать адаптер
    целиком.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from datetime import time as dtime
from typing import TYPE_CHECKING, Any

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)

# Дефолтное время отправки ежедневного дайджеста, если в конфиге
# ``telegram.daily_digest_time`` не задан.
DEFAULT_DIGEST_TIME = "10:00"


class Namespace(BaseNamespace):
    """Аргументы ``telegram-bot``."""

    once: bool
    send_digest_now: bool


class Operation(BaseOperation):
    """Запустить Telegram-бот с long polling.

    Args:
        bot_adapter: опциональный :class:`TelegramBotAdapter` (или любой
            объект с тем же интерфейсом: ``.transport``,
            ``.dispatch_update``, ``.send_digest``). Если ``None`` — операция
            не делает polling и возвращает exit code 1; CLI всегда
            передаёт адаптер из :class:`AppContainer`.
    """

    def __init__(self, bot_adapter: Any | None = None) -> None:
        self._bot_adapter = bot_adapter

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
                "через ``TelegramBotAdapter.send_digest(force=True)``. "
                "В режиме ``--once`` бот завершится сразу после отправки."
            ),
        )

    def run(
        self,
        tool: "HHApplicantTool",
        args: BaseNamespace,
    ) -> int:
        adapter = self._bot_adapter
        if adapter is None:
            logger.error(
                "telegram-bot operation requires a pre-built "
                "TelegramBotAdapter (use AppContainer.create_telegram_bot_adapter)",
            )
            return 1

        # Pre-flight: ``bot_token`` должен быть в конфиге, иначе
        # polling-цикл бесмысленен. Тест ``test_run_returns_1_without_bot_token``
        # требует exit 1 без вызова ``get_updates`` (``adapter.transport``),
        # поэтому проверяем до первого обращения к адаптеру.
        telegram_cfg = tool.config.get("telegram") or {}
        if not telegram_cfg.get("bot_token"):
            logger.error("telegram.bot_token не задан в конфиге")
            return 1

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
                    updates = adapter.transport.get_updates(offset=offset)
                except Exception as exc:  # noqa: BLE001 — внешний сервис
                    logger.error("Polling error: %s", exc)
                    time.sleep(1.0)
                    if once:
                        # В smoke-режиме повторять некогда — выходим с ошибкой.
                        return 1
                    continue

                for update in updates:
                    try:
                        adapter.dispatch_update(update)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        logger.exception("Error handling update: %s", update)
                    update_id = update.get("update_id")
                    if update_id is not None:
                        offset = update_id + 1

                # Проверяем дайджест после каждого успешного цикла polling.
                # В ``--once`` — ровно одна попытка, чтобы поведение было
                # детерминированным и удобным для тестов/cron.
                if not digest_done_this_run:
                    self._maybe_send_digest(
                        tool_config=tool.config,
                        force=send_digest_now,
                        adapter=adapter,
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
        # Unreachable: the ``while True`` loop only exits via
        # ``return`` inside the ``try`` (either ``--once`` returning 0
        # or the ``except`` returning 0 on Ctrl+C). mypy accepts the
        # function as having all paths return because both ``return``
        # statements are inside the ``try``.
        return 0

    # ─── Daily digest ────────────────────────────────────────────────

    def _maybe_send_digest(
        self,
        *,
        tool_config: dict[str, Any],
        force: bool,
        adapter: Any,
        now: datetime | None = None,
    ) -> None:
        """Time-of-day гейт перед :meth:`TelegramBotAdapter.send_digest`.

        Без ``telegram``-конфига — skip. До ``daily_digest_time`` —
        skip. Идемпотентность «раз в день» обеспечивает сам сервис
        (``already_sent_today``), поэтому звать ``send()`` каждый цикл
        безопасно — после успешной отправки он станет no-op.

        Исключения из ``adapter.send_digest`` логируются и
        проглатываются, чтобы polling-цикл не падал (issue #56).
        """
        telegram_cfg = tool_config.get("telegram") or {}
        if not telegram_cfg:
            logger.info("daily_digest: telegram config отсутствует — skip")
            return

        if not self._should_send_digest(tool_config, now=now):
            logger.debug(
                "daily_digest: время ещё не пришло (target=%s) — skip",
                telegram_cfg.get("daily_digest_time", DEFAULT_DIGEST_TIME),
            )
            return

        try:
            result = adapter.send_digest(force=force)
        except Exception:  # noqa: BLE001 — внешний сервис, не валим цикл
            logger.exception("daily_digest: непредвиденная ошибка send()")
            return

        # ``DailyDigestService.send`` возвращает ``DigestResult``;
        # ``result.sent`` атрибут есть у него, но ``result`` может быть
        # и моком — используем ``getattr`` для обоих.
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
    def _should_send_digest(
        tool_config: dict[str, Any],
        now: datetime | None = None,
    ) -> bool:
        """``True``, если текущее время >= ``telegram.daily_digest_time``.

        Сравниваем только ``time()``, дата не важна — гейт срабатывает
        каждый день в ``target`` и позже, пока сервис не «погасит» себя
        идемпотентностью ``already_sent_today``.
        """
        if now is None:
            now = datetime.now()
        target_str = (tool_config.get("telegram") or {}).get(
            "daily_digest_time",
            DEFAULT_DIGEST_TIME,
        )
        target = Operation._parse_digest_time(str(target_str))
        return now.time() >= target

    @staticmethod
    def _parse_digest_time(value: str) -> dtime:
        """Парсит ``"HH:MM"`` в :class:`datetime.time`; падает на дефолт."""
        try:
            parts = value.strip().split(":")
            if len(parts) != 2:
                raise ValueError
            return dtime(int(parts[0]), int(parts[1]))
        except (ValueError, AttributeError):
            logger.warning(
                "Некорректный telegram.daily_digest_time=%r, используем %s",
                value,
                DEFAULT_DIGEST_TIME,
            )
            hh, mm = DEFAULT_DIGEST_TIME.split(":")
            return dtime(int(hh), int(mm))
