"""CommandHandler -- dispatches MAX Bot slash-commands to reply builders.

Mirrors :class:`job_bot.telegram_bot.handlers.command_handler.CommandHandler`
so the two slices stay symmetric: same set of supported commands, same
access-control pattern (allowed_user_ids), same storage-based
``/status`` counts. Unknown / non-text updates get a friendly hint.

Commands supported:
  * ``/start``  -- greeting + list of commands.
  * ``/help``   -- list of commands.
  * ``/stats``  -- count of prepared drafts (best-effort, requires a
                   ``digest`` service that exposes ``collect_groups``).
  * ``/status`` -- high-level counts (negotiations / skipped / drafts).
  * ``/review`` -- placeholder; the full review flow is issue #9.
  * ``/cancel`` -- placeholder; the full review flow is issue #9.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from job_bot.max_bot.models.command import (
    CMD_CANCEL,
    CMD_HELP,
    CMD_REVIEW,
    CMD_START,
    CMD_STATS,
    CMD_STATUS,
    Command,
)
from job_bot.max_bot.models.message import OutgoingMessage
from job_bot.max_bot.ports.transport_port import MaxTransportPort

logger = logging.getLogger(__package__)


class _DigestLike(Protocol):
    """Minimal interface the command handler needs from the digest service."""

    def collect_groups(self) -> list[Any]: ...


class _StorageLike(Protocol):
    """Minimal storage surface used by ``/status``.

    Mirrors the slice-side ``StoragePort`` repositories
    (negotiations / skipped_vacancies / application_drafts), each
    exposing ``count_total()``.
    """

    @property
    def negotiations(self) -> Any: ...

    @property
    def skipped_vacancies(self) -> Any: ...

    @property
    def application_drafts(self) -> Any: ...


class CommandHandler:
    """Handle text-based MAX Bot commands.

    Args:
        storage: a ``StoragePort`` (used for ``/status`` counts). When
            ``None``, ``/status`` returns a friendly "not configured"
            message instead of crashing.
        transport: a :class:`MaxTransportPort` for sending replies and
            for access control (``allowed_user_ids``).
        digest_service: optional service used by ``/stats`` (must
            expose ``collect_groups() -> list[Any]``).
    """

    def __init__(
        self,
        *,
        storage: _StorageLike | None,
        transport: MaxTransportPort,
        digest_service: _DigestLike | None = None,
    ) -> None:
        self._storage = storage
        self._transport = transport
        self._digest = digest_service

    # ─── Public entry point ───────────────────────────────────

    def handle(self, update: dict[str, Any]) -> OutgoingMessage | None:
        """Dispatch a single update; deliver and return the outgoing message.

        The handler builds the reply, ships it via the transport and
        returns the message DTO so callers (``MaxBotService`` / tests)
        can inspect it.
        """
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        user = message.get("from") or {}
        user_id = user.get("id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None or user_id is None:
            return None

        # Access control (mirrors Telegram slice).
        allowed = tuple(getattr(self._transport, "allowed_user_ids", ()) or ())
        if allowed and user_id not in allowed:
            return self._send(
                OutgoingMessage(chat_id=chat_id, text="⛔ Доступ запрещён.")
            )

        if not text:
            return self._send(
                OutgoingMessage(
                    chat_id=chat_id,
                    text=(
                        "🤔 Я понимаю только текстовые команды. "
                        "Используйте /help."
                    ),
                )
            )

        cmd = Command.parse(text)
        if cmd is None:
            return self._send(
                OutgoingMessage(
                    chat_id=chat_id,
                    text=(
                        "❓ Неизвестная команда. Используйте /help "
                        "для списка команд."
                    ),
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
            # Review flow is a follow-up (issue #9). Reply with a
            # placeholder so the user gets immediate feedback.
            return self._send(
                OutgoingMessage(
                    chat_id=chat_id,
                    text="🚧 Review-флоу появится позже (issue #9).",
                )
            )

        # Should be unreachable thanks to Command.parse, but stay
        # defensive.
        return self._send(
            OutgoingMessage(
                chat_id=chat_id,
                text=(
                    "❓ Неизвестная команда. Используйте /help "
                    "для списка команд."
                ),
            )
        )

    def _send(self, message: OutgoingMessage) -> OutgoingMessage:
        """Deliver a :class:`OutgoingMessage` via the transport."""
        try:
            self._transport.send_message(message.chat_id, message.text)
        except Exception:  # noqa: BLE001 - never crash on send
            logger.exception(
                "Failed to send MAX message to chat_id=%s",
                message.chat_id,
            )
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
            "👋 Добро пожаловать в HH Applicant Tool Bot (MAX)!\n\n"
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
            score_suffix = (
                f", средний score: {avg}" if avg is not None else ""
            )
            lines.append(
                f"• {getattr(group, 'profile_name', '?')}: "
                f"{getattr(group, 'total', 0)}"
                f"{tests_suffix}{score_suffix}"
            )
        return "\n".join(lines)

    def _status_text(self) -> str:
        if self._storage is None:
            return "❌ Хранилище не инициализировано."
        try:
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
