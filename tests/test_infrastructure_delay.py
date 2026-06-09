"""Тесты инфраструктурных реализаций задержек и rate-limiter'а."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

from hh_applicant_tool.infrastructure.delay import (
    AsyncDelay,
    RandomDelay,
    TimeDelay,
    TokenBucketRateLimiter,
)

# ─── TimeDelay ─────────────────────────────────────────────────


def test_time_delay_sleep_calls_time_sleep(monkeypatch):
    """TimeDelay.sleep() проксирует в time.sleep()."""
    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    TimeDelay().sleep(0.5)
    assert calls == [0.5]


def test_time_delay_sleep_zero_is_noop(monkeypatch):
    """sleep(0) и sleep(<0) не зовут time.sleep()."""
    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    TimeDelay().sleep(0)
    TimeDelay().sleep(-1.0)
    assert calls == []


# ─── AsyncDelay ────────────────────────────────────────────────


def test_async_delay_sleep_calls_asyncio_sleep(monkeypatch):
    """AsyncDelay.sleep() проксирует в asyncio.sleep()."""
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def driver() -> None:
        await AsyncDelay().sleep(0.25)

    asyncio.run(driver())
    assert calls == [0.25]


def test_async_delay_sleep_zero_is_noop(monkeypatch):
    """sleep(0) и sleep(<0) — без вызова asyncio.sleep()."""
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def driver() -> None:
        await AsyncDelay().sleep(0)
        await AsyncDelay().sleep(-1.0)

    asyncio.run(driver())
    assert calls == []


# ─── TokenBucketRateLimiter ────────────────────────────────────


def test_token_bucket_acquire_first_call_succeeds_immediately(monkeypatch):
    """Первый acquire() не блокирует: токен уже в bucket."""
    clock = MagicMock()
    limiter = TokenBucketRateLimiter(rate=10, burst=1, clock=clock)
    limiter.acquire()
    clock.sleep.assert_not_called()


def test_token_bucket_acquire_exhausted_blocks(monkeypatch):
    """При пустом bucket acquire() зовёт clock.sleep()."""
    clock = MagicMock()
    # burst=1, сразу потребляем токен, потом ожидаем ожидания
    limiter = TokenBucketRateLimiter(rate=10, burst=1, clock=clock)
    limiter.acquire()
    # Второй вызов должен ждать — sleep дёрнут
    limiter.acquire()
    assert clock.sleep.called
    # sleep должен быть вызван с положительным значением
    wait = clock.sleep.call_args[0][0]
    assert wait > 0


def test_token_bucket_uses_passed_clock(monkeypatch):
    """TokenBucketRateLimiter использует переданный clock."""
    custom_clock = MagicMock()
    limiter = TokenBucketRateLimiter(rate=2, burst=1, clock=custom_clock)
    limiter.acquire()
    limiter.acquire()
    # При ожидании должен зваться наш кастомный clock
    assert custom_clock.sleep.called


def test_token_bucket_async_acquire_blocks(monkeypatch):
    """async_acquire() при пустом bucket ждёт через asyncio.sleep()."""
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    limiter = TokenBucketRateLimiter(rate=2, burst=1, clock=TimeDelay())
    limiter.acquire()

    async def driver() -> None:
        await limiter.async_acquire()

    asyncio.run(driver())
    assert calls
    assert calls[0] > 0


# ─── RandomDelay ───────────────────────────────────────────────


def test_random_delay_sleep_uses_base_delay(monkeypatch):
    """RandomDelay.sleep() зовёт delay.sleep() с базовой задержкой ± jitter."""
    base_delay = 1.0
    sleep_mock = MagicMock()
    delay = RandomDelay(base_delay=base_delay, jitter=0.0, delay=sleep_mock)
    delay.sleep()
    sleep_mock.sleep.assert_called_once()
    actual = sleep_mock.sleep.call_args[0][0]
    assert actual == base_delay


def test_random_delay_sleep_respects_jitter(monkeypatch):
    """При jitter=0.1 фактическая задержка в пределах [base*0.9, base*1.1]."""
    base_delay = 1.0
    sleep_mock = MagicMock()
    delay = RandomDelay(base_delay=base_delay, jitter=0.1, delay=sleep_mock)
    delay.sleep()
    actual = sleep_mock.sleep.call_args[0][0]
    assert 0.9 <= actual <= 1.1


def test_random_delay_negative_clamped_to_zero(monkeypatch):
    """Если base_delay=0 — задержка не может быть отрицательной."""
    sleep_mock = MagicMock()
    delay = RandomDelay(base_delay=0.0, jitter=1.0, delay=sleep_mock)
    delay.sleep()
    actual = sleep_mock.sleep.call_args[0][0]
    assert actual >= 0.0


def test_random_delay_async_sleep_uses_asyncio(monkeypatch):
    """RandomDelay.async_sleep() проксирует в asyncio.sleep()."""
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    delay = RandomDelay(base_delay=1.0, jitter=0.0)
    asyncio.run(delay.async_sleep())
    assert calls == [1.0]
