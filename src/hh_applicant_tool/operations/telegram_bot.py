"""CLI-операция ``telegram-bot`` (issue #7).

Запускает Telegram-бот в режиме long polling.
Слушает команды /start, /help, /status, /stats, /review, /cancel.
Фильтрует пользователей по allowed_user_ids.
После каждого цикла polling проверяет, не пора ли отправить
ежедневный дайджест (``DailyDigestService``).

CLI-флаги:
* ``--once`` — обработать один цикл polling и завершиться.
* ``--send-digest-now`` — принудительно отправить дайджест
  (``force=True`` в :meth:`DailyDigestService.send`).

Graceful shutdown по Ctrl+C.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from datetime import time as dtime
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation
from ..services.daily_digest import DailyDigestService
from ..telegram import (
    TelegramTransport,
    TelegramTransportConfig,
    TelegramTransportError,
)

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)

# Дефолтное время отправки ежедневного дайджеста, если в конфиге
# ``telegram.daily_digest_time`` не задан.
DEFAULT_DIGEST_TIME = "10:00"

# Сообщение-заглушка для фич review-flow (issue #9).
_REVIEW_PLACEHOLDER = "🚧 Review-флоу появится позже (issue #9)"


class Namespace(BaseNamespace):
    """Аргументы ``telegram-bot``."""

    once: bool
    send_digest_now: bool


class Operation(BaseOperation):
    """Запустить Telegram-бот с long polling."""

    def __init__(self) -> None:
        # Прокидывается из тестов или из ``run()`` (DI-стиль).
        # Если ``None`` — собираем в ``run()`` из ``tool``.
        self._digest_service: DailyDigestService | None = None

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
                "через ``DailyDigestService.send(force=True)``. "
                "В режиме ``--once`` бот завершится сразу после отправки."
            ),
        )

    def run(
        self,
        tool: "HHApplicantTool",
        args: BaseNamespace,
    ) -> int:
        telegram_cfg = tool.config.get("telegram") or {}
        bot_token = telegram_cfg.get("bot_token")
        if not bot_token:
            logger.error("telegram.bot_token не задан в конфиге")
            return 1

        raw_timeout = telegram_cfg.get("poll_timeout", 30)
        try:
            poll_timeout = int(raw_timeout)
        except (ValueError, TypeError):
            poll_timeout = 30

        allowed_raw = telegram_cfg.get("allowed_user_ids") or []
        allowed_user_ids = tuple(int(uid) for uid in allowed_raw)

        config = TelegramTransportConfig(
            bot_token=bot_token,
            poll_timeout=poll_timeout,
            allowed_user_ids=allowed_user_ids,
        )

        transport = TelegramTransport(config=config)

        # DI-friendly: если сервис не инжектирован (в проде), собираем здесь.
        if self._digest_service is None:
            self._digest_service = self._build_digest_service(tool, transport)

        once = bool(getattr(args, "once", False))
        send_digest_now = bool(getattr(args, "send_digest_now", False))

        mode_label = "single-cycle" if once else "long polling"
        logger.info("Telegram bot started (%s)...", mode_label)
        print(f"🤖 Telegram bot started ({mode_label}). Press Ctrl+C to stop.")

        offset: int | None = None
        backoff = 1
        digest_done_this_run = False
        try:
            while True:
                try:
                    updates = transport.get_updates(offset=offset)
                    backoff = 1  # reset on success
                except TelegramTransportError as exc:
                    logger.error("Polling error: %s", exc)
                    time.sleep(min(backoff, 60))
                    backoff *= 2
                    if once:
                        # В smoke-режиме повторять некогда — выходим с ошибкой.
                        return 1
                    continue

                for update in updates:
                    try:
                        self._handle_update(update, transport, tool)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        logger.exception("Error handling update: %s", update)
                        # Try to notify user about the error
                        message = update.get("message") or {}
                        chat_id = message.get("chat", {}).get("id")
                        if chat_id:
                            try:
                                transport.send_message(
                                    chat_id,
                                    "❌ Произошла ошибка при обработке команды.",
                                )
                            except TelegramTransportError:
                                pass  # Best effort

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

    # ─── Daily digest ────────────────────────────────────────────────

    def _build_digest_service(
        self,
        tool: "HHApplicantTool",
        transport: TelegramTransport,
    ) -> DailyDigestService:
        """Фабрика ``DailyDigestService`` из текущего ``tool``.

        Выделено в метод, чтобы подменить в тестах через DI-инжекцию
        (``Operation(digest_service=mock)``).
        """
        return DailyDigestService(
            storage=tool.storage,
            transport=transport,
            config=tool.config,
        )

    def _maybe_send_digest(
        self,
        tool_config: dict,
        force: bool,
        now: datetime | None = None,
    ) -> None:
        """Проверяет время и при необходимости вызывает ``DailyDigestService``.

        Time-of-day гейт: ждём ``telegram.daily_digest_time`` (default
        :data:`DEFAULT_DIGEST_TIME`). Идемпотентность «раз в день» обеспечивает
        сам сервис (``already_sent_today``), поэтому звать ``send()`` каждый
        цикл безопасно — после успешной отправки он станет no-op.
        """
        if self._digest_service is None:
            # Сервис не сконфигурирован (например, telegram.bot_token не задан
            # и мы вышли раньше). На всякий случай — тихо пропускаем.
            return

        # Без telegram-конфига дайджест слать некуда: сервис сам вернёт
        # ``no_telegram_config``, но мы экономим один SQL-проход.
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
            result = self._digest_service.send(force=force)
        except Exception:  # noqa: BLE001 — внешний сервис, не валим цикл
            logger.exception("daily_digest: непредвиденная ошибка send()")
            return

        if result.sent:
            logger.info(
                "daily_digest: отправлен (drafts=%s, force=%s)",
                result.total_drafts,
                force,
            )
        else:
            logger.info(
                "daily_digest: пропущен (%s, force=%s)",
                result.skipped_reason,
                force,
            )

    @staticmethod
    def _should_send_digest(
        tool_config: dict,
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

    # ─── Update handling ─────────────────────────────────────────────

    def _handle_update(
        self,
        update: dict,
        transport: TelegramTransport,
        tool: "HHApplicantTool",
    ) -> None:
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        user = message.get("from") or {}
        user_id = user.get("id")
        chat_id = message.get("chat", {}).get("id")

        if not chat_id or not user_id:
            return

        if (
            transport.allowed_user_ids
            and user_id not in transport.allowed_user_ids
        ):
            logger.warning("Access denied for user %s", user_id)
            try:
                transport.send_message(chat_id, "⛔ Доступ запрещён.")
            except TelegramTransportError as exc:
                logger.error("Failed to send access denied message: %s", exc)
            return

        if not text:
            # User sent non-text message (photo, sticker, voice, etc.)
            logger.info("Received non-text message from user %s", user_id)
            try:
                transport.send_message(
                    chat_id,
                    "🤔 Я понимаю только текстовые команды. Используйте /help.",
                )
            except TelegramTransportError:
                pass
            return

        if text == "/start":
            reply = self._build_start_message()
        elif text == "/help":
            reply = self._build_help_message()
        elif text == "/status":
            reply = self._build_status_message(tool)
        elif text == "/stats":
            reply = self._build_stats_message()
        elif text == "/review":
            # Placeholder: полное state-machine будет в issue #9.
            reply = _REVIEW_PLACEHOLDER
        elif text == "/cancel":
            # Placeholder: отмена review-сессии появится в issue #9.
            reply = _REVIEW_PLACEHOLDER
        else:
            logger.info("Unknown command from user %s: %s", user_id, text)
            try:
                transport.send_message(
                    chat_id,
                    "❓ Неизвестная команда. Используйте /help для списка команд.",
                )
            except TelegramTransportError:
                pass
            return

        try:
            transport.send_message(chat_id, reply)
        except TelegramTransportError as exc:
            logger.error("Failed to send message: %s", exc)

    # ─── Reply builders ──────────────────────────────────────────────

    def _build_commands_list(self) -> str:
        return (
            "Доступные команды:\n"
            "/start — приветствие и список команд\n"
            "/status — общая статистика из базы данных\n"
            "/stats — черновики, сгруппированные по профилям\n"
            "/review — начать сессию ревью черновиков (в разработке)\n"
            "/cancel — отменить текущую сессию ревью (в разработке)\n"
            "/help — список команд"
        )

    def _build_start_message(self) -> str:
        return f"👋 Добро пожаловать в HH Applicant Tool Bot!\n\n{self._build_commands_list()}"

    def _build_help_message(self) -> str:
        return self._build_commands_list()

    def _build_status_message(self, tool: "HHApplicantTool") -> str:
        try:
            negotiations_count = tool.storage.negotiations.count_total()
            skipped_count = tool.storage.skipped_vacancies.count_total()
            drafts_count = tool.storage.application_drafts.count_total()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            logger.exception("Failed to get statistics")
            return "❌ Не удалось получить статистику."

        return (
            f"📊 Статистика:\n"
            f"• Переговоры: {negotiations_count}\n"
            f"• Пропущено: {skipped_count}\n"
            f"• Черновики: {drafts_count}"
        )

    def _build_stats_message(self) -> str:
        """Короткая сводка по подготовленным черновикам.

        Использует :meth:`DailyDigestService.collect_groups`, чтобы не
        дублировать SQL и иметь один источник истины для группировки.
        """
        if self._digest_service is None:
            return "❌ Сервис дайджеста не инициализирован."

        try:
            groups = self._digest_service.collect_groups()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            logger.exception("Failed to collect draft stats")
            return "❌ Не удалось получить статистику черновиков."

        if not groups:
            return "📭 Нет подготовленных черновиков к ревью."

        total = sum(g.total for g in groups)
        lines = [f"📊 Черновики к ревью: {total}"]
        for group in groups:
            tests_suffix = (
                f" (с тестами: {group.with_tests}, без: {group.without_tests})"
            )
            score_suffix = (
                f", средний score: {group.average_score}"
                if group.average_score is not None
                else ""
            )
            lines.append(
                f"• {group.profile_name}: {group.total}"
                f"{tests_suffix}{score_suffix}"
            )
        return "\n".join(lines)
