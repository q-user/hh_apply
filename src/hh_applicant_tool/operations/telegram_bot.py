"""CLI-операция ``telegram-bot`` (issue #7).

Запускает Telegram-бот в режиме long polling.
Слушает команды /start, /status, /help.
Фильтрует пользователей по allowed_user_ids.
Graceful shutdown по Ctrl+C.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation
from ..telegram import (
    TelegramTransport,
    TelegramTransportConfig,
    TelegramTransportError,
)

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    pass


class Operation(BaseOperation):
    """Запустить Telegram-бот с long polling."""

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

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

        logger.info("Telegram bot started (long polling)...")
        print("🤖 Telegram bot started. Press Ctrl+C to stop.")

        offset: int | None = None
        backoff = 1
        try:
            while True:
                try:
                    updates = transport.get_updates(offset=offset)
                    backoff = 1  # reset on success
                except TelegramTransportError as exc:
                    logger.error("Polling error: %s", exc)
                    time.sleep(min(backoff, 60))
                    backoff *= 2
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
        except KeyboardInterrupt:
            logger.info("Telegram bot is shutting down...")

        print("⛔ Telegram bot stopped.")
        return 0

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

    def _build_commands_list(self) -> str:
        return (
            "Доступные команды:\n"
            "/start — приветствие и список команд\n"
            "/status — статистика из базы данных\n"
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
