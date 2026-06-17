"""CLI-операция ``apply-worker`` (VSA rewrite, issue #147).

The VSA-typed version of the legacy ``apply-worker`` op. The op takes
its dependencies (the application_submit slice's ``WorkerService``) via
constructor injection.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)

DEFAULT_IDLE_SLEEP_SECONDS = 5.0


class _WorkerSlice(Protocol):
    """Minimal slice contract for ``apply-worker``."""

    @property
    def worker(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``apply-worker``."""

    once: bool
    max_jobs: int | None
    worker_id: str | None
    idle_sleep: float
    no_telegram: bool


class Operation(BaseOperation):
    """Запустить фоновый воркер асинхронной отправки откликов."""

    def __init__(self, slice_: _WorkerSlice | None = None) -> None:
        self._slice = slice_

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
                "Пауза (в секундах) между опросами пустой очереди. "
                f"По умолчанию: {DEFAULT_IDLE_SLEEP_SECONDS}."
            ),
        )
        parser.add_argument(
            "--no-telegram",
            action="store_true",
            help="Отключить Telegram-нотификации (для тестов/debug).",
        )

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("apply-worker requires a slice with a worker")
            return 1
        once = bool(getattr(args, "once", False))
        max_jobs = getattr(args, "max_jobs", None)
        if once:
            max_jobs = 1

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
            "apply-worker finished: processed=%d succeeded=%d failed=%d",
            stats.processed,
            stats.succeeded,
            stats.failed,
        )
        print(
            f"✅ apply-worker: processed={stats.processed} "
            f"succeeded={stats.succeeded} failed={stats.failed}",
            flush=True,
        )
        return 0


__all__ = ("Operation", "Namespace")
