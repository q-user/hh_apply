"""Background worker for async application submission (issue #10).

.. versionchanged:: 2.0
   Moved from ``hh_applicant_tool.services.apply_worker`` to
   ``job_bot.application_submit.services.apply_worker_service``
   as part of the VSA switchover (issue #77).

Подбирает queued задачи из apply_jobs, атомарно блокирует и
отправляет через переданный apply_one callable. Backoff
(max_attempts=5): 5/15/60 мин. Идемпотентность — locked_at/
locked_by (одна задача на draft, см. UNIQUE в схеме).
"""

from __future__ import annotations

import logging
import socket
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.application_draft import (
    ApplicationDraftModel,
)
from hh_applicant_tool.storage.models.apply_job import ApplyJobModel
from hh_applicant_tool.telegram.transport import (
    TelegramTransport,
    TelegramTransportError,
)

if TYPE_CHECKING:
    from hh_applicant_tool.application.ports import Clock, DelayPort

logger = logging.getLogger(__package__)

# ─── Доменные ошибки и контракты ────────────────────────────────────


class RetryableError(Exception):
    """Ошибка, после которой задачу можно повторить позже (сеть, 5xx, капча)."""


class FatalError(Exception):
    """Ошибка, после которой повтор бессмыслен (400/403/404, баг)."""


class ApplyOneDraftFn(Protocol):
    """Отправить один черновик на hh.ru.

    Контракт: успех → ``None``; ошибка → :class:`RetryableError`
    или :class:`FatalError`.
    """

    def __call__(self, draft: ApplicationDraftModel) -> Any: ...


# ─── Backoff-стратегия ────────────────────────────────────────────────

# Паузы (в секундах) по числу УЖЕ сделанных попыток:
# 1 -> 5min, 2 -> 15min, 3+ -> 1h (give-up на 5-й).
_BACKOFF_SECONDS: tuple[int, ...] = (
    5 * 60,
    15 * 60,
    60 * 60,
    60 * 60,
)

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_IDLE_SLEEP_SECONDS = 5.0
# Залипший lock старше этого — подбираем (предыдущий воркер умер).
LOCK_TIMEOUT_SECONDS = 30 * 60


def _backoff_for_attempt(attempt: int) -> int:
    """Задержка по индексу attempt - 1 (с клипом на последний)."""
    if attempt < 1:
        return 0
    return _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]


# ─── DTO результатов ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ProcessResult:
    """Результат :meth:`ApplyWorkerService.process_one`."""

    status: str  # "succeeded"/"failed"/"skipped"
    job_id: int
    draft_id: int
    attempts: int
    last_error: str | None = None


@dataclass
class RunStats:
    """Статистика одного прогона :meth:`ApplyWorkerService.run`."""

    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0
    idle_loops: int = 0
    last_result: ProcessResult | None = None


# ─── Сервис ───────────────────────────────────────────────────────────


class ApplyWorkerService:
    """Фоновый воркер асинхронной отправки откликов (issue #10).

    Зависимости (DI): storage, apply_one (HTTP, капча, Playwright),
    config (секция telegram), clock/delay (порты),
    transport (опц.), worker_id (для apply_jobs.locked_by).
    """

    def __init__(
        self,
        storage: StorageFacade,
        apply_one: ApplyOneDraftFn,
        config: Mapping[str, Any] | None = None,
        *,
        clock: "Clock | None" = None,
        delay: "DelayPort | None" = None,
        transport: TelegramTransport | None = None,
        worker_id: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ):
        self._storage = storage
        self._apply_one = apply_one
        self._config: Mapping[str, Any] = config if config is not None else {}
        self._clock: Clock = clock or self._default_clock()
        self._delay: DelayPort = delay or self._default_delay()
        self._transport = transport
        self._worker_id = worker_id or self._default_worker_id()
        self._max_attempts = max_attempts
        self._stop_requested = False

    @staticmethod
    def _default_clock() -> "Clock":
        from hh_applicant_tool.infrastructure.time import SystemClock

        return SystemClock()

    @staticmethod
    def _default_delay() -> "DelayPort":
        from hh_applicant_tool.infrastructure.delay import TimeDelay

        return TimeDelay()

    @staticmethod
    def _default_worker_id() -> str:
        try:
            host = socket.gethostname()
        except OSError:
            host = "worker"
        return f"{host}:{uuid.uuid4().hex[:8]}"

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def clock(self) -> "Clock":
        return self._clock

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def stop(self) -> None:
        """Запросить остановку :meth:`run` после текущей итерации."""
        self._stop_requested = True

    def process_one(self) -> ProcessResult | None:
        """Один тик воркера. None если очередь пуста."""
        job = self._claim_next_job()
        if job is None:
            return None
        return self._process_claimed_job(job)

    def run(
        self,
        *,
        max_jobs: int | None = None,
        stop_when_idle: bool = False,
        idle_sleep_seconds: float = DEFAULT_IDLE_SLEEP_SECONDS,
    ) -> RunStats:
        """Запустить цикл воркера. max_jobs форсит stop_when_idle."""
        self._stop_requested = False
        stats = RunStats()
        if max_jobs is not None:
            stop_when_idle = True

        try:
            while not self._stop_requested:
                if max_jobs is not None and stats.processed >= max_jobs:
                    break

                try:
                    result = self.process_one()
                except Exception as ex:  # noqa: BLE001
                    # Непредвиденная ошибка — логируем и продолжаем, чтобы
                    # один битый job не убил весь воркер.
                    logger.exception(
                        "apply-worker: непредвиденная ошибка: %s", ex
                    )
                    self._delay.sleep(idle_sleep_seconds)
                    continue

                if result is None:
                    stats.idle_loops += 1
                    if stop_when_idle:
                        break
                    self._delay.sleep(idle_sleep_seconds)
                    continue

                stats.processed += 1
                stats.last_result = result
                if result.status == "succeeded":
                    stats.succeeded += 1
                elif result.status == "failed":
                    stats.failed += 1
                elif result.status == "skipped":
                    stats.retried += 1
        except KeyboardInterrupt:
            logger.info("apply-worker: SIGINT, выхожу gracefully")
            self._stop_requested = True

        return stats

    def _claim_next_job(self) -> ApplyJobModel | None:
        """Атомарно выбрать и заблокировать очередную задачу (SELECT ... FOR UPDATE).

        Возвращает загруженную модель ApplyJobModel с обновлёнными locked_* полями,
        или None, если подходящих задач нет.
        """
        now_str = self._iso_now()
        cutoff_str = self._iso_now_minus(LOCK_TIMEOUT_SECONDS)

        # Используем SELECT ... FOR UPDATE для предотвращения race condition
        # при параллельной работе нескольких воркеров (issue #44).
        job = self._storage.apply_jobs.claim_next_job(
            worker_id=self._worker_id,
            now_str=now_str,
            cutoff_str=cutoff_str,
        )
        if job is None:
            return None

        # Заблокировать job внутри той же транзакции
        self._storage.apply_jobs.lock_job(
            job_id=job.id,
            worker_id=self._worker_id,
            locked_at=now_str,
        )
        self._commit()

        # Перезагрузить job с обновлёнными полями
        return self._storage.apply_jobs.get(job.id)

    def _process_claimed_job(self, job: ApplyJobModel) -> ProcessResult:
        """Применить + обновить статусы job/draft."""
        # attempts уже инкрементирован в lock_job (issue #44)

        draft = self._load_draft(job.draft_id)
        if draft is None:
            return self._mark_failed(
                job,
                draft=None,
                error=f"application_draft id={job.draft_id} не найден",
            )

        draft.status = "applying"
        self._storage.application_drafts.save(draft)
        self._commit()

        try:
            self._apply_one(draft)
        except FatalError as ex:
            logger.error("apply-worker: FatalError job=%d: %s", job.id, ex)
            return self._mark_failed(job, draft, str(ex))
        except RetryableError as ex:
            logger.warning(
                "apply-worker: RetryableError job=%d attempt=%d: %s",
                job.id,
                job.attempts,
                ex,
            )
            return self._handle_retryable(job, draft, str(ex))
        except Exception as ex:  # noqa: BLE001
            # Неизвестная ошибка — retryable (консервативно).
            logger.exception(
                "apply-worker: неизвестная ошибка job=%d: %s",
                job.id,
                ex,
            )
            return self._handle_retryable(job, draft, f"unexpected: {ex!r}")
        else:
            return self._mark_succeeded(job, draft)

    def _mark_succeeded(
        self, job: ApplyJobModel, draft: ApplicationDraftModel
    ) -> ProcessResult:
        job.status = "succeeded"
        job.last_error = None
        job.locked_at = None
        job.locked_by = None
        self._storage.apply_jobs.save(job)

        draft.status = "applied"
        draft.last_error = None
        if draft.hh_response_url is None:
            draft.hh_response_url = f"https://hh.ru/vacancy/{draft.vacancy_id}"
        self._storage.application_drafts.save(draft)
        self._commit()

        self._notify_success(draft, chat_id=job.chat_id)
        return ProcessResult(
            status="succeeded",
            job_id=job.id or 0,
            draft_id=draft.id or 0,
            attempts=job.attempts,
        )

    def _mark_failed(
        self,
        job: ApplyJobModel,
        draft: ApplicationDraftModel | None,
        error: str,
    ) -> ProcessResult:
        job.status = "failed"
        job.last_error = error
        job.locked_at = None
        job.locked_by = None
        self._storage.apply_jobs.save(job)

        if draft is not None:
            draft.status = "failed"
            draft.last_error = error
            self._storage.application_drafts.save(draft)
        self._commit()

        self._notify_failure(draft, error, chat_id=job.chat_id)
        return ProcessResult(
            status="failed",
            job_id=job.id or 0,
            draft_id=draft.id if draft is not None else 0,
            attempts=job.attempts,
            last_error=error,
        )

    def _handle_retryable(
        self,
        job: ApplyJobModel,
        draft: ApplicationDraftModel,
        error: str,
    ) -> ProcessResult:
        """Обработать :class:`RetryableError`."""
        if job.attempts >= self._max_attempts:
            logger.warning(
                "apply-worker: max_attempts=%d достигнут (job=%d), give up",
                self._max_attempts,
                job.id,
            )
            return self._mark_failed(job, draft, error)

        # Запланировать ретрай.
        delay_seconds = _backoff_for_attempt(job.attempts)
        next_at = self._now() + timedelta(seconds=delay_seconds)
        job.status = "queued"
        job.last_error = error
        job.locked_at = None
        job.locked_by = None
        job.next_attempt_at = self._isoformat(next_at)
        self._storage.apply_jobs.save(job)
        # Draft остаётся в applying до следующей попытки.
        self._commit()

        return ProcessResult(
            status="skipped",
            job_id=job.id or 0,
            draft_id=draft.id or 0,
            attempts=job.attempts,
            last_error=error,
        )

    def _resolve_notification_chat_ids(self) -> list[int]:
        """chat_id для нотификации (приоритет как у :class:`DailyDigestService`)."""
        if self._transport is None:
            return []
        cfg = self._config.get("telegram") or {}
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

    def _notify(
        self,
        kind: str,
        draft: ApplicationDraftModel | None,
        error: str,
        chat_id: int | None = None,
    ) -> None:
        """Единая отправка success/failure (Telegram-формат из issue).

        Если передан chat_id — используем его (пер-драфтовое уведомление, issue #43).
        Иначе — fallback на конфиг (_resolve_notification_chat_ids).
        """
        if chat_id is not None:
            chat_ids = [chat_id]
        else:
            chat_ids = self._resolve_notification_chat_ids()
        if not chat_ids:
            return
        meta = self._vacancy_meta(draft) if draft is not None else None
        if kind == "success":
            assert meta is not None
            text = f"✅ Отклик отправлен:\n{meta['name']} — {meta['employer']}\n{meta['url']}"
        elif meta is not None:
            text = (
                f"❌ Не удалось отправить отклик:\n"
                f"{meta['name']} — {meta['employer']}\nПричина: {error}"
            )
        else:
            text = f"❌ Не удалось отправить отклик:\nПричина: {error}"
        for cid in chat_ids:
            try:
                self._transport.send_message(cid, text)  # type: ignore[union-attr]
            except TelegramTransportError as ex:
                logger.warning(
                    "apply-worker: notify chat_id=%d failed: %s",
                    cid,
                    ex,
                )

    def _notify_success(
        self, draft: ApplicationDraftModel, chat_id: int | None = None
    ) -> None:
        self._notify("success", draft, "", chat_id=chat_id)

    def _notify_failure(
        self,
        draft: ApplicationDraftModel | None,
        error: str,
        chat_id: int | None = None,
    ) -> None:
        self._notify("failure", draft, error, chat_id=chat_id)

    @staticmethod
    def _vacancy_meta(draft: ApplicationDraftModel) -> dict[str, str]:
        """Имя/employer/URL из full_vacancy_json (с fallback)."""
        vacancy: dict[str, Any] = (
            draft.full_vacancy_json if draft.full_vacancy_json else {}
        )
        name = str(vacancy.get("name") or f"vacancy #{draft.vacancy_id}")
        employer_obj = vacancy.get("employer") or {}
        if isinstance(employer_obj, dict):
            employer_name = employer_obj.get("name") or "(без названия)"
        else:
            employer_name = "(без названия)"
        url = str(
            vacancy.get("alternate_url")
            or f"https://hh.ru/vacancy/{draft.vacancy_id}"
        )
        return {
            "name": name,
            "employer": str(employer_name),
            "url": url,
        }

    def _load_draft(self, draft_id: int) -> ApplicationDraftModel | None:
        return self._storage.application_drafts.get(draft_id)

    def _commit(self) -> None:
        """Commit (для in-memory БД тестов это важно)."""
        self._storage.apply_jobs.commit()

    def _now(self) -> datetime:
        return self._clock.now()

    def _iso_now(self) -> str:
        return self._isoformat(self._now())

    @staticmethod
    def _isoformat(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _iso_to_dt(value: str) -> datetime:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")

    def _iso_now_minus(self, seconds: int) -> str:
        return self._isoformat(self._now() - timedelta(seconds=seconds))
