"""CommandHandler -- dispatches slash-commands to reply builders.

Commands supported:
  * ``/start``  -- greeting + list of commands.
  * ``/help``   -- list of commands.
  * ``/stats``  -- count of prepared drafts (delegates to the digest
                   service for grouping).
  * ``/status`` -- high-level counts (negotiations / skipped / drafts).
  * ``/review`` -- hand-off to the review service.
  * ``/cancel`` -- hand-off to the review service (same dispatch).

Unknown / non-text updates get a friendly hint message.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from hh_applicant_tool.telegram.transport import TelegramTransportError
from job_bot.shared.storage.ports import StoragePort
from job_bot.telegram_bot.models.command import (
    CMD_CANCEL,
    CMD_HELP,
    CMD_REVIEW,
    CMD_START,
    CMD_STATS,
    CMD_STATUS,
    Command,
)
from job_bot.telegram_bot.models.message import OutgoingMessage
from job_bot.telegram_bot.ports.transport_port import TelegramTransportPort

logger = logging.getLogger(__package__)


class _ReviewLike(Protocol):
    """Minimal interface the command handler needs from the review service."""

    def process_message(self, update: dict[str, Any]) -> list[Any]: ...
    def process_callback(self, update: dict[str, Any]) -> list[Any]: ...


class _DigestLike(Protocol):
    """Minimal interface the command handler needs from the digest service."""

    def collect_groups(self) -> list[Any]: ...


class CommandHandler:
    """Handle text-based Telegram commands.

    Args:
        storage: a ``StoragePort`` (used for ``/status`` counts).
        transport: a ``TelegramTransportPort`` for sending replies.
        digest_service: optional service used by ``/stats``.
        review_service: optional service used by ``/review`` and ``/cancel``.
    """

    def __init__(
        self,
        *,
        storage: StoragePort,
        transport: TelegramTransportPort,
        digest_service: _DigestLike | None = None,
        review_service: _ReviewLike | None = None,
    ) -> None:
        self._storage = storage
        self._transport = transport
        self._digest = digest_service
        self._review = review_service

    # ─── Public entry point ───────────────────────────────────

    def handle(self, update: dict[str, Any]) -> OutgoingMessage | None:
        """Dispatch a single update; deliver and return the outgoing message.

        The handler builds the reply, ships it via the transport and
        returns the message DTO so callers (BotService / tests) can
        inspect it.
        """
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        user = message.get("from") or {}
        user_id = user.get("id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None or user_id is None:
            return None

        # Access control.
        allowed = tuple(self._transport.allowed_user_ids or ())
        if allowed and user_id not in allowed:
            return self._send(
                OutgoingMessage(chat_id=chat_id, text="⛔ Доступ запрещён.")
            )

        if not text:
            return self._send(
                OutgoingMessage(
                    chat_id=chat_id,
                    text="🤔 Я понимаю только текстовые команды. Используйте /help.",
                )
            )

        cmd = Command.parse(text)
        if cmd is None:
            return self._send(
                OutgoingMessage(
                    chat_id=chat_id,
                    text="❓ Неизвестная команда. Используйте /help для списка команд.",
                )
            )

        if cmd.name == CMD_START:
            return self._send(
                OutgoingMessage(chat_id=chat_id, text=self._start_text())
            )
        if cmd.name == CMD_HELP:
            return self._send(
                OutgoingMessage(chat_id=chat_id, text=self._help_text())
            )
        if cmd.name == CMD_STATS:
            return self._send(
                OutgoingMessage(chat_id=chat_id, text=self._stats_text())
            )
        if cmd.name == CMD_STATUS:
            return self._send(
                OutgoingMessage(chat_id=chat_id, text=self._status_text())
            )
        if cmd.name in (CMD_REVIEW, CMD_CANCEL):
            if self._review is None:
                return self._send(
                    OutgoingMessage(
                        chat_id=chat_id,
                        text="🚧 Review-флоу появится позже (issue #9)",
                    )
                )
            # Hand off to the review service. The review service ships
            # its own outgoing messages.
            try:
                self._review.process_message(update)
            except Exception:  # noqa: BLE001 - never crash the bot
                logger.exception("Review service failed")
            return None

        # Should be unreachable thanks to Command.parse, but stay defensive.
        return self._send(
            OutgoingMessage(
                chat_id=chat_id,
                text="❓ Неизвестная команда. Используйте /help для списка команд.",
            )
        )

    def _send(self, message: OutgoingMessage) -> OutgoingMessage:
        """Deliver a :class:`OutgoingMessage` via the transport."""
        try:
            self._transport.send_message(message.chat_id, message.text)
        except TelegramTransportError as exc:
            logger.error("Failed to send message: %s", exc)
        return message

    # ─── Reply builders ───────────────────────────────────────

    @staticmethod
    def _commands_list() -> str:
        return (
            "Доступные команды:\n"
            "/start — приветствие и список команд\n"
            "/status — общая статистика из базы данных\n"
            "/stats — черновики, сгруппированные по профилям\n"
            "/review — начать сессию ревью черновиков\n"
            "/cancel — отменить текущую сессию ревью\n"
            "/help — список команд"
        )

    @classmethod
    def _start_text(cls) -> str:
        return (
            "👋 Добро пожаловать в HH Applicant Tool Bot!\n\n"
            + cls._commands_list()
        )

    @classmethod
    def _help_text(cls) -> str:
        return cls._commands_list()

    def _stats_text(self) -> str:
        if self._digest is None:
            return "❌ Сервис дайджеста не инициализирован."
        try:
            groups = self._digest.collect_groups()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to collect draft stats")
            return "❌ Не удалось получить статистику черновиков."
        if not groups:
            return "📭 Нет подготовленных черновиков к ревью."
        total = sum(getattr(g, "total", 0) for g in groups)
        lines = [f"📊 Черновики к ревью: {total}"]
        for group in groups:
            tests_suffix = (
                f" (с тестами: {getattr(group, 'with_tests', 0)}, "
                f"без: {getattr(group, 'without_tests', 0)})"
            )
            avg = getattr(group, "average_score", None)
            score_suffix = f", средний score: {avg}" if avg is not None else ""
            lines.append(
                f"• {getattr(group, 'profile_name', '?')}: "
                f"{getattr(group, 'total', 0)}"
                f"{tests_suffix}{score_suffix}"
            )
        return "\n".join(lines)

    def _status_text(self) -> str:
        try:
            # The injected ``StoragePort`` already exposes the same
            # repository properties (``negotiations``,
            # ``skipped_vacancies``, ``application_drafts``) as the
            # legacy ``StorageFacade``; use it directly instead of
            # wrapping it again. Keeps the slice DB-agnostic and
            # removes the ``# type: ignore[arg-type]`` on the facade
            # constructor (issue #74).
            negotiations_count = self._storage.negotiations.count_total()
            skipped_count = self._storage.skipped_vacancies.count_total()
            drafts_count = self._storage.application_drafts.count_total()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to get statistics")
            return "❌ Не удалось получить статистику."
        return (
            f"📊 Статистика:\n"
            f"• Переговоры: {negotiations_count}\n"
            f"• Пропущено: {skipped_count}\n"
            f"• Черновики: {drafts_count}"
        )
