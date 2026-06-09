"""CLI-операция ``apply-worker`` (issue #10).

Тонкий адаптер: парсит argparse → собирает
:class:`hh_applicant_tool.services.apply_worker.ApplyWorkerService` →
запускает :meth:`ApplyWorkerService.run`.

Флаги: ``--once``, ``--max-jobs N``, ``--worker-id ID``,
``--idle-sleep SECONDS``, ``--no-telegram``. Graceful shutdown по Ctrl+C.
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation
from ..services.apply_worker import (
    ApplyWorkerService,
    make_default_apply_one,
)
from ..storage.facade import StorageFacade
from ..telegram import (
    TelegramTransport,
    TelegramTransportConfig,
    TelegramTransportError,
)

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

        # apply_one шлёт один черновик; ApplyToVacanciesUseCase
        # оперирует списком вакансий, поэтому отдельная обёртка.
        apply_one = make_default_apply_one(tool.api_client)
        transport = self._build_transport(tool, args)

        worker = ApplyWorkerService(
            storage=StorageFacade(tool.db),
            apply_one=apply_one,
            config=tool.config or {},
            transport=transport,
            worker_id=getattr(args, "worker_id", None),
        )

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
                idle_sleep_seconds=float(
                    getattr(args, "idle_sleep", DEFAULT_IDLE_SLEEP_SECONDS)
                ),
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

    # ─── DI-хелперы ──────────────────────────────────────────────

    def _build_transport(
        self,
        tool: "HHApplicantTool",
        args: BaseNamespace,
    ) -> TelegramTransport | None:
        """Сконструировать :class:`TelegramTransport` или вернуть ``None``.

        Возвращает ``None`` если передан ``--no-telegram`` или в конфиге
        нет ``telegram.bot_token``.
        """
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
        try:
            return TelegramTransport(
                config=TelegramTransportConfig(
                    bot_token=cfg["bot_token"],
                    poll_timeout=poll_timeout,
                    allowed_user_ids=allowed,
                )
            )
        except TelegramTransportError as ex:
            logger.warning("apply-worker: TelegramTransport init failed: %s", ex)
            return None


__all__ = ("Operation", "Namespace")
