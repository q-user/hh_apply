"""Тесты инфраструктурных реализаций времени и токенов отмены."""

from __future__ import annotations

import asyncio
import datetime
import threading
import time

from hh_applicant_tool.infrastructure.time import (
    AsyncioCancellationToken,
    SystemClock,
    ThreadingCancellationToken,
)

# ─── SystemClock ────────────────────────────────────────────────


def test_system_clock_now_returns_datetime():
    """SystemClock.now() возвращает объект datetime."""
    clock = SystemClock()
    now = clock.now()
    assert isinstance(now, datetime.datetime)


def test_system_clock_now_is_current():
    """now() близко к реальному времени (в пределах секунды)."""
    clock = SystemClock()
    before = datetime.datetime.now()
    got = clock.now()
    after = datetime.datetime.now()
    assert before <= got <= after


def test_system_clock_sleep_calls_time_sleep(monkeypatch):
    """SystemClock.sleep() проксирует вызов в time.sleep()."""
    clock = SystemClock()
    calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    clock.sleep(0.25)
    assert calls == [0.25]


# ─── ThreadingCancellationToken ────────────────────────────────


def test_threading_token_initially_not_cancelled():
    token = ThreadingCancellationToken()
    assert token.is_cancelled is False


def test_threading_token_cancel_sets_flag():
    token = ThreadingCancellationToken()
    token.cancel()
    assert token.is_cancelled is True


def test_threading_token_cancel_idempotent():
    """Повторный cancel() безопасен."""
    token = ThreadingCancellationToken()
    token.cancel()
    token.cancel()
    assert token.is_cancelled is True


def test_threading_token_callback_invoked_on_cancel():
    token = ThreadingCancellationToken()
    calls: list[int] = []
    token.register_callback(lambda: calls.append(1))
    token.register_callback(lambda: calls.append(2))
    token.cancel()
    assert sorted(calls) == [1, 2]


def test_threading_token_callback_invoked_immediately_if_already_cancelled():
    """Регистрация колбэка после cancel() вызывает его немедленно."""
    token = ThreadingCancellationToken()
    token.cancel()
    called: list[bool] = []
    token.register_callback(lambda: called.append(True))
    assert called == [True]


def test_threading_token_callback_error_does_not_break_others():
    """Ошибка в одном колбэке не мешает вызову остальных."""
    token = ThreadingCancellationToken()
    called: list[str] = []
    token.register_callback(lambda: called.append("first"))
    token.register_callback(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    token.register_callback(lambda: called.append("third"))
    # cancel() глотает ошибки и продолжает
    token.cancel()
    assert "first" in called
    assert "third" in called


def test_threading_token_shares_external_event():
    """Можно передать свой threading.Event — токен реагирует на его set()."""
    event = threading.Event()
    token = ThreadingCancellationToken(event=event)
    assert token.is_cancelled is False
    event.set()
    assert token.is_cancelled is True


# ─── AsyncioCancellationToken ──────────────────────────────────


def test_asyncio_token_initially_not_cancelled():
    token = AsyncioCancellationToken()
    assert token.is_cancelled is False


def test_asyncio_token_cancel_sets_flag():
    token = AsyncioCancellationToken()
    token.cancel()
    assert token.is_cancelled is True


def test_asyncio_token_callback_invoked_on_cancel():
    token = AsyncioCancellationToken()
    calls: list[int] = []
    token.register_callback(lambda: calls.append(1))
    token.register_callback(lambda: calls.append(2))
    token.cancel()
    assert sorted(calls) == [1, 2]


def test_asyncio_token_callback_invoked_immediately_if_already_cancelled():
    token = AsyncioCancellationToken()
    token.cancel()
    called: list[bool] = []
    token.register_callback(lambda: called.append(True))
    assert called == [True]


def test_asyncio_token_callback_error_swallowed():
    token = AsyncioCancellationToken()
    called: list[str] = []
    token.register_callback(lambda: called.append("a"))
    token.register_callback(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    token.register_callback(lambda: called.append("c"))
    token.cancel()
    assert "a" in called
    assert "c" in called


def test_asyncio_token_wait_resolves_after_cancel():
    """wait() возвращается, как только вызван cancel()."""
    token = AsyncioCancellationToken()

    async def driver() -> None:
        # Планируем cancel в той же event loop через 50 мс
        loop = asyncio.get_event_loop()
        loop.call_later(0.05, token.cancel)
        # wait() должен вернуться после cancel
        await asyncio.wait_for(token.wait(), timeout=1.0)

    asyncio.run(driver())
    assert token.is_cancelled is True


def test_asyncio_token_wait_already_cancelled_returns_immediately():
    token = AsyncioCancellationToken()
    token.cancel()
    # Не должно висеть — cancel уже вызван
    asyncio.run(asyncio.wait_for(token.wait(), timeout=0.5))
    assert token.is_cancelled is True
