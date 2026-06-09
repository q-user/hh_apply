"""Тесты фонового воркера асинхронной отправки откликов (issue #10).

Покрывает:
- happy path (apply_one возвращает успех → job succeeded, draft applied);
- retryable error с backoff (попытки 1-3 → next_attempt_at + retry);
- give-up на max_attempts (job failed, draft failed);
- fatal error сразу (job failed, draft failed, без ретраев);
- блокировка (locked_at/locked_by, повторный claim не даёт дубль);
- идемпотентность (повторный process_one после succeeded = None);
- уведомления в Telegram (mock transport, проверка send_message);
- ``--once``-эквивалент: ``stop_when_idle``;
- цикл ``run`` с ``max_jobs`` (лимит задач);
- graceful stop через ``stop()``.

Дефолтная реализация ``make_default_apply_one`` (классификация API-ошибок)
тестируется отдельно в ``test_apply_worker_default.py``.

Все тесты на in-memory SQLite (``storage`` fixture) с моками
``apply_one``, ``transport`` и фиксированными часами.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from hh_applicant_tool.services.apply_worker import (
    DEFAULT_MAX_ATTEMPTS,
    ApplyWorkerService,
    FatalError,
    RetryableError,
    RunStats,
)
from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.application_draft import (
    ApplicationDraftModel,
)
from hh_applicant_tool.storage.models.apply_job import ApplyJobModel
from hh_applicant_tool.telegram.transport import TelegramTransport

CHAT_ID = 12345

# ─── Фикстуры и хелперы ──────────────────────────────────────────────


class _FixedClock:
    """Детерминированные часы — ``now()`` отдаёт фиксированный момент,
    ``sleep`` — no-op."""

    def __init__(self, base: datetime | None = None) -> None:
        self._now = base or datetime(2026, 6, 9, 10, 0, 0)

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:  # noqa: D401
        return None

    def advance(self, seconds: int) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def _no_sleep_delay() -> MagicMock:
    """Delay-порт, который ничего не ждёт (тесты не блокируются)."""
    return MagicMock()


def _make_transport() -> MagicMock:
    transport = MagicMock(spec=TelegramTransport)
    transport.send_message.return_value = {"message_id": 1, "ok": True}
    return transport


def _make_service(
    conn: sqlite3.Connection,
    *,
    apply_one: Any = None,
    transport: MagicMock | None = None,
    config: dict | None = None,
    clock: _FixedClock | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    worker_id: str = "test-worker",
) -> ApplyWorkerService:
    if apply_one is None:
        apply_one = MagicMock()
    if transport is None:
        transport = _make_transport()
    if config is None:
        config = {"telegram": {"chat_id": CHAT_ID}}
    if clock is None:
        clock = _FixedClock()
    return ApplyWorkerService(
        storage=StorageFacade(conn),
        apply_one=apply_one,
        config=config,
        clock=clock,
        delay=_no_sleep_delay(),
        transport=transport,
        worker_id=worker_id,
        max_attempts=max_attempts,
    )


def _make_draft(
    facade: StorageFacade,
    *,
    vacancy_id: int = 1,
    status: str = "queued",
    has_test: bool = False,
    cover_letter: str | None = "Готов работать!",
    vacancy_name: str = "Senior Django Developer",
    employer_name: str = "Example LLC",
    vacancy_url: str | None = None,
) -> int:
    """Сохраняет черновик и возвращает его ``id``."""
    full_vacancy: dict[str, Any] = {
        "id": vacancy_id,
        "name": vacancy_name,
        "employer": {"name": employer_name},
        "alternate_url": vacancy_url or f"https://hh.ru/vacancy/{vacancy_id}",
    }
    draft = ApplicationDraftModel(
        resume_id="r1",
        vacancy_id=vacancy_id,
        status=status,
        has_test=has_test,
        cover_letter=cover_letter,
        cover_letter_status="generated",
        full_vacancy_json=full_vacancy,
    )
    facade.application_drafts.save(draft)
    facade.application_drafts.commit()
    row = facade.application_drafts.conn.execute(
        "SELECT id FROM application_drafts WHERE vacancy_id=?",
        (vacancy_id,),
    ).fetchone()
    return row["id"]


def _make_job(
    facade: StorageFacade,
    draft_id: int,
    *,
    status: str = "queued",
    attempts: int = 0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    next_attempt_at: str | None = None,
) -> int:
    """Сохраняет job и возвращает его ``id``."""
    job = ApplyJobModel(
        draft_id=draft_id,
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
    )
    if next_attempt_at is not None:
        job.next_attempt_at = next_attempt_at
    facade.apply_jobs.save(job)
    facade.apply_jobs.commit()
    row = facade.apply_jobs.conn.execute(
        "SELECT id FROM apply_jobs WHERE draft_id=?",
        (draft_id,),
    ).fetchone()
    return row["id"]


def _get_job(facade: StorageFacade, job_id: int) -> ApplyJobModel:
    return facade.apply_jobs.get(job_id)  # type: ignore[return-value]


def _get_draft(facade: StorageFacade, draft_id: int) -> ApplicationDraftModel:
    return facade.application_drafts.get(draft_id)  # type: ignore[return-value]


# ─── Импорт / инстанцирование ────────────────────────────────────────


def test_service_can_be_imported():
    """Сервис экспортируется из ``services`` и принимает DI-аргументы."""
    from hh_applicant_tool.services import ApplyWorkerService as Imported

    assert Imported is ApplyWorkerService


def test_exports_in_services_package():
    """Все нужные символы реэкспортируются из services."""
    from hh_applicant_tool.services import (
        ApplyOneDraftFn,
        ApplyWorkerService,
        FatalError,
        ProcessResult,
        RetryableError,
        RunStats,
        make_default_apply_one,
    )

    assert ApplyWorkerService is not None
    assert RetryableError is not None
    assert FatalError is not None
    assert ProcessResult is not None
    assert RunStats is not None
    assert ApplyOneDraftFn is not None
    assert callable(make_default_apply_one)


def test_service_instantiation_with_minimal_args(storage: sqlite3.Connection):
    """Минимальный конструктор: storage + apply_one. Остальное — fallback."""
    svc = ApplyWorkerService(
        storage=StorageFacade(storage),
        apply_one=MagicMock(),
    )
    assert svc.clock is not None
    assert svc.worker_id is not None  # auto-generated
    assert svc.max_attempts == DEFAULT_MAX_ATTEMPTS


def test_custom_worker_id_is_preserved(storage: sqlite3.Connection):
    """Явный ``worker_id`` сохраняется в свойстве и попадает в ``locked_by``."""
    svc = _make_service(storage, worker_id="my-host-42")
    assert svc.worker_id == "my-host-42"


# ─── Happy path ───────────────────────────────────────────────────────


def test_process_one_happy_path_marks_succeeded(
    storage: sqlite3.Connection,
):
    """Успешный apply → job succeeded, draft applied, Telegram notify."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=1)
    job_id = _make_job(facade, draft_id)

    apply_one = MagicMock()
    transport = _make_transport()
    svc = _make_service(storage, apply_one=apply_one, transport=transport)

    result = svc.process_one()

    assert result is not None
    assert result.status == "succeeded"
    assert result.job_id == job_id
    assert result.draft_id == draft_id
    assert result.attempts == 1
    apply_one.assert_called_once()
    # apply_one получил draft
    sent_draft = apply_one.call_args[0][0]
    assert sent_draft.id == draft_id

    # job в succeeded
    job = _get_job(facade, job_id)
    assert job.status == "succeeded"
    assert job.last_error is None
    assert job.locked_at is None
    assert job.locked_by is None
    assert job.attempts == 1

    # draft в applied + URL проставлен
    draft = _get_draft(facade, draft_id)
    assert draft.status == "applied"
    assert draft.last_error is None
    assert draft.hh_response_url == "https://hh.ru/vacancy/1"

    # Telegram notify: success
    assert transport.send_message.call_count == 1
    args, _ = transport.send_message.call_args
    chat_id, text = args
    assert chat_id == CHAT_ID
    assert "✅ Отклик отправлен" in text
    assert "Senior Django Developer" in text
    assert "Example LLC" in text


def test_process_one_idempotent_after_succeeded(
    storage: sqlite3.Connection,
):
    """После succeeded повторный process_one не подбирает ту же задачу."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=2)
    _make_job(facade, draft_id)

    apply_one = MagicMock()
    svc = _make_service(storage, apply_one=apply_one)
    svc.process_one()
    apply_one.assert_called_once()

    # Повторный process_one: задача в succeeded, не подбирается.
    result2 = svc.process_one()
    assert result2 is None
    apply_one.assert_called_once()  # второй раз не звали


# ─── Retryable error → backoff + retry ───────────────────────────────


def test_retryable_error_schedules_retry_with_backoff(
    storage: sqlite3.Connection,
):
    """RetryableError → status=queued, attempts++, next_attempt_at в будущем."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=10)
    job_id = _make_job(facade, draft_id)

    apply_one = MagicMock(side_effect=RetryableError("network blip"))
    clock = _FixedClock(datetime(2026, 6, 9, 10, 0, 0))
    svc = _make_service(storage, apply_one=apply_one, clock=clock)

    result = svc.process_one()

    assert result is not None
    assert result.status == "skipped"
    assert result.attempts == 1
    assert "network blip" in (result.last_error or "")

    job = _get_job(facade, job_id)
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.last_error == "network blip"
    assert job.locked_at is None
    assert job.locked_by is None

    # Backoff после 1-й попытки → 5 минут (300 сек).
    expected_next = (clock.now() + timedelta(minutes=5)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert job.next_attempt_at == expected_next

    # draft остаётся "applying" (в процессе).
    draft = _get_draft(facade, draft_id)
    assert draft.status == "applying"


def test_retryable_backoff_increases_with_attempts(
    storage: sqlite3.Connection,
):
    """Backoff растёт с каждой попыткой: 0 → 5m → 15m → 1h."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=11)
    _make_job(facade, draft_id)

    apply_one = MagicMock(side_effect=RetryableError("blip"))
    clock = _FixedClock()
    svc = _make_service(
        storage, apply_one=apply_one, clock=clock, max_attempts=10
    )

    # Попытка 1 → next_attempt_at = now + 5 min
    svc.process_one()
    job = list(facade.apply_jobs.find(draft_id=draft_id))[0]
    assert job.attempts == 1
    assert job.next_attempt_at == (clock.now() + timedelta(minutes=5)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Попытка 2 (после сдвига часов) → next_attempt_at = now + 15 min
    clock.advance(3600)
    svc.process_one()
    job = list(facade.apply_jobs.find(draft_id=draft_id))[0]
    assert job.attempts == 2
    assert job.next_attempt_at == (
        clock.now() + timedelta(minutes=15)
    ).strftime("%Y-%m-%d %H:%M:%S")

    # Попытка 3 → + 1h
    clock.advance(3600)
    svc.process_one()
    job = list(facade.apply_jobs.find(draft_id=draft_id))[0]
    assert job.attempts == 3
    assert job.next_attempt_at == (clock.now() + timedelta(hours=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def test_retryable_error_does_not_send_telegram_notification(
    storage: sqlite3.Connection,
):
    """Retryable: Telegram notify не вызывается (только на финал)."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=12)
    _make_job(facade, draft_id)

    apply_one = MagicMock(side_effect=RetryableError("rate limit"))
    transport = _make_transport()
    svc = _make_service(storage, apply_one=apply_one, transport=transport)

    svc.process_one()
    transport.send_message.assert_not_called()


# ─── Give-up: max_attempts ───────────────────────────────────────────


def test_retryable_gives_up_after_max_attempts(
    storage: sqlite3.Connection,
):
    """После ``max_attempts`` RetryableError → job failed, draft failed."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=20)
    job_id = _make_job(facade, draft_id, max_attempts=3)

    apply_one = MagicMock(side_effect=RetryableError("perma-broken"))
    transport = _make_transport()
    svc = _make_service(
        storage,
        apply_one=apply_one,
        transport=transport,
        max_attempts=3,
    )

    # Попытки 1, 2 → retry. Попытка 3 → give up.
    r1 = svc.process_one()
    assert r1 is not None and r1.status == "skipped"
    clock = svc.clock  # type: ignore[assignment]
    clock.advance(3600)

    r2 = svc.process_one()
    assert r2 is not None and r2.status == "skipped"
    clock.advance(3600)

    r3 = svc.process_one()
    assert r3 is not None
    assert r3.status == "failed"
    assert r3.attempts == 3

    job = _get_job(facade, job_id)
    assert job.status == "failed"
    assert job.last_error == "perma-broken"
    assert job.attempts == 3

    draft = _get_draft(facade, draft_id)
    assert draft.status == "failed"
    assert draft.last_error == "perma-broken"

    # Telegram notify: failure
    assert transport.send_message.call_count == 1
    args, _ = transport.send_message.call_args
    chat_id, text = args
    assert chat_id == CHAT_ID
    assert "❌ Не удалось отправить отклик" in text
    assert "perma-broken" in text


# ─── Fatal error: no retry ───────────────────────────────────────────


def test_fatal_error_marks_failed_without_retry(
    storage: sqlite3.Connection,
):
    """FatalError → job failed, draft failed, Telegram notify, без ретраев."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=30)
    job_id = _make_job(facade, draft_id)

    apply_one = MagicMock(side_effect=FatalError("vacancy 404 not found"))
    transport = _make_transport()
    svc = _make_service(storage, apply_one=apply_one, transport=transport)

    result = svc.process_one()
    assert result is not None
    assert result.status == "failed"
    assert result.attempts == 1
    assert "404" in (result.last_error or "")

    job = _get_job(facade, job_id)
    assert job.status == "failed"
    assert job.last_error == "vacancy 404 not found"
    assert job.attempts == 1  # счётчик всё равно инкрементнут

    draft = _get_draft(facade, draft_id)
    assert draft.status == "failed"

    transport.send_message.assert_called_once()
    _, text = transport.send_message.call_args[0]
    assert "❌" in text
    assert "vacancy 404 not found" in text


def test_unknown_exception_is_treated_as_retryable(
    storage: sqlite3.Connection,
):
    """Любое «неизвестное» исключение → Retryable (консервативно)."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=31)
    job_id = _make_job(facade, draft_id)

    apply_one = MagicMock(side_effect=ValueError("???"))
    svc = _make_service(storage, apply_one=apply_one)

    result = svc.process_one()
    assert result is not None
    assert result.status == "skipped"  # retryable
    job = _get_job(facade, job_id)
    assert job.status == "queued"
    assert "ValueError" in (job.last_error or "")


# ─── Блокировка и concurrency ────────────────────────────────────────


def test_locked_job_not_claimed_by_another_worker(
    storage: sqlite3.Connection,
):
    """Свежий lock чужого воркера блокирует claim."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=40)
    _make_job(facade, draft_id)

    # Вручную ставим "running" с залочкой от worker-A
    job = list(facade.apply_jobs.find(draft_id=draft_id))[0]
    job.status = "running"
    job.locked_at = "2026-06-09 09:55:00"  # 5 мин назад, lock свежий
    job.locked_by = "worker-A"
    facade.apply_jobs.save(job)
    facade.apply_jobs.commit()

    apply_one = MagicMock()
    svc = _make_service(storage, apply_one=apply_one, worker_id="worker-B")

    # worker-B не должен подобрать эту задачу.
    assert svc.process_one() is None
    apply_one.assert_not_called()


def test_stale_lock_is_recovered(
    storage: sqlite3.Connection,
):
    """Залипший lock (старше LOCK_TIMEOUT) подбирается другим воркером."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=41)
    _make_job(facade, draft_id)

    job = list(facade.apply_jobs.find(draft_id=draft_id))[0]
    job.status = "running"
    # 2 часа назад — точно старше LOCK_TIMEOUT (30 мин)
    job.locked_at = "2026-06-09 08:00:00"
    job.locked_by = "worker-A"
    job.attempts = 1
    facade.apply_jobs.save(job)
    facade.apply_jobs.commit()

    apply_one = MagicMock()
    svc = _make_service(
        storage,
        apply_one=apply_one,
        worker_id="worker-B",
        clock=_FixedClock(datetime(2026, 6, 9, 10, 0, 0)),
    )

    result = svc.process_one()
    assert result is not None
    assert result.status == "succeeded"
    apply_one.assert_called_once()

    # Новый lock уже от worker-B
    new_job = list(facade.apply_jobs.find(draft_id=draft_id))[0]
    assert new_job.status == "succeeded"
    assert new_job.locked_by is None  # unlock при success


def test_process_one_returns_none_when_queue_empty(
    storage: sqlite3.Connection,
):
    """Пустая очередь → process_one возвращает ``None``."""
    facade = StorageFacade(storage)
    _make_draft(facade, vacancy_id=99)
    # Job не создаём.

    apply_one = MagicMock()
    svc = _make_service(storage, apply_one=apply_one)
    assert svc.process_one() is None
    apply_one.assert_not_called()


def test_process_one_skips_future_next_attempt_at(
    storage: sqlite3.Connection,
):
    """Job с ``next_attempt_at`` в будущем не подбирается."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=50)
    _make_job(
        facade,
        draft_id,
        next_attempt_at="2099-01-01 00:00:00",
    )

    apply_one = MagicMock()
    svc = _make_service(storage, apply_one=apply_one)
    assert svc.process_one() is None
    apply_one.assert_not_called()


# ─── Telegram-нотификации ────────────────────────────────────────────


def test_notification_uses_explicit_chat_id(
    storage: sqlite3.Connection,
):
    """``apply_notification_chat_id`` имеет приоритет."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=60)
    _make_job(facade, draft_id)

    apply_one = MagicMock()
    transport = _make_transport()
    svc = _make_service(
        storage,
        apply_one=apply_one,
        transport=transport,
        config={
            "telegram": {
                "apply_notification_chat_id": 999,
                "chat_id": CHAT_ID,
            }
        },
    )

    svc.process_one()
    args, _ = transport.send_message.call_args
    assert args[0] == 999


def test_notification_falls_back_to_allowed_user_ids(
    storage: sqlite3.Connection,
):
    """Если ни ``chat_id``, ни ``digest_chat_id`` не заданы —
    fallback на ``allowed_user_ids[0]``.
    """
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=61)
    _make_job(facade, draft_id)

    apply_one = MagicMock()
    transport = _make_transport()
    svc = _make_service(
        storage,
        apply_one=apply_one,
        transport=transport,
        config={"telegram": {"allowed_user_ids": [777, 888]}},
    )

    svc.process_one()
    args, _ = transport.send_message.call_args
    assert args[0] == 777


def test_no_notification_when_transport_is_none(
    storage: sqlite3.Connection,
):
    """Без transport ошибок нет (просто молча)."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=62)
    _make_job(facade, draft_id)

    apply_one = MagicMock()
    svc = _make_service(
        storage,
        apply_one=apply_one,
        transport=None,
        config={},
    )

    result = svc.process_one()
    assert result is not None and result.status == "succeeded"


def test_telegram_error_does_not_fail_job(
    storage: sqlite3.Connection,
):
    """Сбой Telegram не помечает job как failed (notify — best effort)."""
    from hh_applicant_tool.telegram.transport import TelegramTransportError

    facade = StorageFacade(storage)
    draft_id = _make_draft(facade, vacancy_id=63)
    job_id = _make_job(facade, draft_id)

    apply_one = MagicMock()
    transport = _make_transport()
    transport.send_message.side_effect = TelegramTransportError("net fail")
    svc = _make_service(storage, apply_one=apply_one, transport=transport)

    result = svc.process_one()
    # Job всё равно succeeded.
    assert result is not None
    assert result.status == "succeeded"
    job = _get_job(facade, job_id)
    assert job.status == "succeeded"


# ─── Цикл run() ──────────────────────────────────────────────────────


def test_run_processes_multiple_jobs_then_stops_when_idle(
    storage: sqlite3.Connection,
):
    """run() обрабатывает несколько задач и выходит, когда очередь пуста."""
    facade = StorageFacade(storage)
    for v in (1, 2, 3):
        d_id = _make_draft(facade, vacancy_id=v)
        _make_job(facade, d_id)

    apply_one = MagicMock()
    svc = _make_service(storage, apply_one=apply_one)

    stats = svc.run(stop_when_idle=True, idle_sleep_seconds=0)

    assert isinstance(stats, RunStats)
    assert stats.processed == 3
    assert stats.succeeded == 3
    assert stats.failed == 0
    assert apply_one.call_count == 3


def test_run_respects_max_jobs_limit(
    storage: sqlite3.Connection,
):
    """``--max-jobs N`` ограничивает количество обработанных задач."""
    facade = StorageFacade(storage)
    for v in (10, 11, 12, 13, 14):
        d_id = _make_draft(facade, vacancy_id=v)
        _make_job(facade, d_id)

    apply_one = MagicMock()
    svc = _make_service(storage, apply_one=apply_one)

    stats = svc.run(max_jobs=2, idle_sleep_seconds=0)
    assert stats.processed == 2
    assert apply_one.call_count == 2

    # Остальные три в queued.
    remaining = list(facade.apply_jobs.find(status="queued"))
    assert len(remaining) == 3


def test_run_stops_via_stop_method(
    storage: sqlite3.Connection,
):
    """``stop()`` прерывает цикл после текущей итерации."""
    facade = StorageFacade(storage)
    for v in (20, 21):
        d_id = _make_draft(facade, vacancy_id=v)
        _make_job(facade, d_id)

    apply_one = MagicMock()
    svc = _make_service(storage, apply_one=apply_one)

    # Хук: после первого apply_one — stop().
    apply_one.side_effect = lambda *_a, **_kw: svc.stop()

    stats = svc.run(idle_sleep_seconds=0)
    assert stats.processed == 1
    assert svc.stop_requested is True


def test_run_returns_none_idle_loops_when_queue_empty(
    storage: sqlite3.Connection,
):
    """Пустая очередь → idle_loops растёт, processed=0."""
    apply_one = MagicMock()
    svc = _make_service(storage, apply_one=apply_one)

    stats = svc.run(max_jobs=1, idle_sleep_seconds=0)
    assert stats.processed == 0
    assert stats.idle_loops >= 1


# ─── Сценарий с draft=None (странно, но возможно) ────────────────────


def test_missing_draft_marks_job_failed(
    storage: sqlite3.Connection,
):
    """Если draft удалён между enqueue и claim → job failed без ретрая."""
    facade = StorageFacade(storage)
    # Создаём draft, чтобы получить id, но не сохраняем job.
    draft_id = _make_draft(facade, vacancy_id=80)
    # Создаём job на этот draft.
    _make_job(facade, draft_id)
    # Удаляем draft (имитируем расхождение данных) — по id,
    # потому что ``delete(draft)`` использует in-memory ``draft.id``,
    # который не обновляется после INSERT с AUTOINCREMENT.
    facade.application_drafts.delete(draft_id)
    facade.application_drafts.commit()

    apply_one = MagicMock()
    svc = _make_service(storage, apply_one=apply_one)
    result = svc.process_one()

    assert result is not None
    assert result.status == "failed"
    assert "не найден" in (result.last_error or "").lower()
    apply_one.assert_not_called()


# ─── Vacuum-meta (имя/employer) для уведомлений ──────────────────────


def test_notification_uses_fallback_names_when_no_vacancy_json(
    storage: sqlite3.Connection,
):
    """Если ``full_vacancy_json`` пустой — fallback на ``vacancy #{id}``."""
    facade = StorageFacade(storage)
    draft_id = _make_draft(
        facade, vacancy_id=90, vacancy_name=None, employer_name=None
    )
    # Подменим full_vacancy_json на пустой dict
    draft = _get_draft(facade, draft_id)
    draft.full_vacancy_json = {}
    facade.application_drafts.save(draft)
    facade.application_drafts.commit()
    _make_job(facade, draft_id)

    apply_one = MagicMock()
    transport = _make_transport()
    svc = _make_service(storage, apply_one=apply_one, transport=transport)
    svc.process_one()

    args, _ = transport.send_message.call_args
    _, text = args
    # Есть fallback "vacancy #90" и "(без названия)" для работодателя.
    assert "vacancy #90" in text or "90" in text
    assert "(без названия)" in text
