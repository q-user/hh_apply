"""CLI-операция ``apply-worker`` (issue #10).

Тонкий адаптер: парсит argparse → собирает
:class:`job_bot.application_submit.slice.ApplicationSubmitSlice` через
:class:`AppContainer` → запускает
:meth:`ApplicationSubmitSlice.worker.run`.

Флаги: ``--once``, ``--max-jobs N``, ``--worker-id ID``,
``--idle-sleep SECONDS``, ``--no-telegram``. Graceful shutdown по Ctrl+C.

This is the VSA-backed rewrite of the legacy ``ApplyWorkerService``
worker loop (issue #77). The legacy service was retired in favour of
:class:`job_bot.application_submit.services.worker_service.WorkerService`.
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from ..container import AppContainer
from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)

DEFAULT_IDLE_SLEEP_SECONDS = 5.0


class Namespace(BaseNamespace):
    """Аргументы ``apply-worker``."""

    once: bool
    max_jobs: int | None
    worker_id: str | None
    idle_sleep: float
    no_telegram: bool


class Operation(BaseOperation):
    """Запустить фоновый воркер асинхронной отправки откликов."""

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help=(
                "Обработать ровно один job (или выйти, если очередь "
                "пуста). Удобно для cron и smoke-тестов."
            ),
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=None,
            help=(
                "Обработать максимум N задач и выйти. "
                "По умолчанию — бесконечно (до Ctrl+C)."
            ),
        )
        parser.add_argument(
            "--worker-id",
            type=str,
            default=None,
            help=(
                "Идентификатор воркера (apply_jobs.locked_by). "
                "По умолчанию: '<hostname>:<random>'."
            ),
        )
        parser.add_argument(
            "--idle-sleep",
            type=float,
            default=DEFAULT_IDLE_SLEEP_SECONDS,
            help=(
                f"Пауза (в секундах) между опросами пустой очереди. "
                f"По умолчанию: {DEFAULT_IDLE_SLEEP_SECONDS}."
            ),
        )
        parser.add_argument(
            "--no-telegram",
            action="store_true",
            help="Отключить Telegram-нотификации (для тестов/debug).",
        )

    def run(
        self,
        tool: "HHApplicantTool",
        args: BaseNamespace,
    ) -> int:
        once = bool(getattr(args, "once", False))
        max_jobs = getattr(args, "max_jobs", None)
        if once:
            max_jobs = 1

        idle_sleep = float(
            getattr(args, "idle_sleep", DEFAULT_IDLE_SLEEP_SECONDS)
        )

        # Build the VSA slice. The apply-one handler needs the
        # ``requests.Session`` (for XSRF extraction on test drafts)
        # and the optional cover-letter AI client (used by
        # ``VacancyTestsService`` to generate answers).
        container = AppContainer(tool)
        slice_ = container._get_application_submit_slice_with(
            session=tool.session,
            xsrf_token=tool.xsrf_token,
            ai_client=tool.get_cover_letter_ai(),
            notifier=None
            if bool(getattr(args, "no_telegram", False))
            else _build_notifier(tool, args),
            worker_id=getattr(args, "worker_id", None),
            idle_sleep_seconds=idle_sleep,
        )
        worker = slice_.worker

        mode = (
            "single job (--once)"
            if once
            else (
                f"up to {max_jobs} jobs"
                if max_jobs is not None
                else "long-running"
            )
        )
        logger.info(
            "apply-worker started (%s, worker_id=%s, max_jobs=%s)",
            mode,
            worker.worker_id,
            max_jobs,
        )
        print(
            f"🚀 apply-worker started ({mode}). Press Ctrl+C to stop.",
            flush=True,
        )

        try:
            stats = worker.run(
                max_jobs=max_jobs,
                stop_when_idle=once,
            )
        except KeyboardInterrupt:
            logger.info("apply-worker: KeyboardInterrupt")
            print("\n⛔ apply-worker stopped.", flush=True)
            return 130  # SIGINT

        logger.info(
            "apply-worker finished: processed=%d succeeded=%d failed=%d "
            "retried=%d idle_loops=%d",
            stats.processed,
            stats.succeeded,
            stats.failed,
            stats.retried,
            stats.idle_loops,
        )
        print(
            f"✅ apply-worker: processed={stats.processed} "
            f"succeeded={stats.succeeded} failed={stats.failed}",
            flush=True,
        )
        return 0


def _build_notifier(tool: "HHApplicantTool", args: BaseNamespace):
    """Return a notifier callable or ``None``.

    The VSA ``WorkerService`` accepts ``notifier: Callable[[str, str], None]``
    which receives ``(kind, text)`` for success/failure. We forward to
    the legacy ``TelegramTransport`` if a bot token is configured and
    ``--no-telegram`` is not set.
    """
    from hh_applicant_tool.telegram import (
        TelegramTransport,
        TelegramTransportConfig,
        TelegramTransportError,
    )

    if bool(getattr(args, "no_telegram", False)):
        return None
    cfg = (tool.config or {}).get("telegram") or {}
    if not cfg.get("bot_token"):
        return None
    try:
        poll_timeout = int(cfg.get("poll_timeout", 30))
    except (ValueError, TypeError):
        poll_timeout = 30
    allowed = tuple(int(u) for u in (cfg.get("allowed_user_ids") or []))
    proxy_url = cfg.get("proxy_url")
    try:
        transport = TelegramTransport(
            config=TelegramTransportConfig(
                bot_token=cfg["bot_token"],
                poll_timeout=poll_timeout,
                allowed_user_ids=allowed,
                proxy_url=proxy_url,
            )
        )
    except TelegramTransportError as ex:
        logger.warning("apply-worker: TelegramTransport init failed: %s", ex)
        return None

    # Resolve notification chat_id (same priority as the legacy worker).
    chat_ids = _resolve_chat_ids(tool)
    if not chat_ids:
        return None

    def _notifier(kind: str, text: str) -> None:
        for cid in chat_ids:
            try:
                transport.send_message(cid, text)
            except TelegramTransportError as ex:
                logger.warning(
                    "apply-worker: notify chat_id=%d failed: %s", cid, ex
                )

    return _notifier


def _resolve_chat_ids(tool: "HHApplicantTool") -> list[int]:
    """Return notification chat_ids based on the telegram config.

    Priority (most explicit to most permissive):
    ``apply_notification_chat_id`` → ``digest_chat_id`` → ``chat_id`` →
    first entry of ``allowed_user_ids`` (the bot owner).
    """
    cfg = (tool.config or {}).get("telegram") or {}
    for key in (
        "apply_notification_chat_id",
        "digest_chat_id",
        "chat_id",
    ):
        val = cfg.get(key)
        if val is not None and not isinstance(val, list):
            return [int(val)]
    allowed = cfg.get("allowed_user_ids") or []
    if allowed:
        return [int(allowed[0])]
    return []


__all__ = ("Operation", "Namespace")
