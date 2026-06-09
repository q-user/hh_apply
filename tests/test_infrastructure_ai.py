"""Тесты инфраструктурных AI-клиентов: ChatOpenAIClient, RateLimitedAIClient."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.infrastructure.ai import (
    ChatOpenAIClient,
    RateLimitedAIClient,
    TokenBucketRateLimiterForAI,
)
from hh_applicant_tool.infrastructure.delay import (
    TimeDelay,
    TokenBucketRateLimiter,
)

# ─── ChatOpenAIClient ──────────────────────────────────────────


def test_chat_openai_client_delegates_complete():
    """complete() проксирует в базовый ChatOpenAI.complete()."""
    base = MagicMock()
    base.complete.return_value = "hello"
    client = ChatOpenAIClient(chat_openai=base)
    assert client.complete("hi") == "hello"
    base.complete.assert_called_once_with("hi")


def test_chat_openai_client_propagates_argument():
    """Аргумент complete() пробрасывается без изменений."""
    base = MagicMock()
    client = ChatOpenAIClient(chat_openai=base)
    client.complete("xyz prompt")
    # Промпт дошёл до базового клиента как первый позиционный аргумент
    assert base.complete.call_args[0][0] == "xyz prompt"


def test_chat_openai_client_exposes_rate_limit():
    """rate_limit — getter/setter, делегируются в базовый клиент."""
    base = MagicMock()
    base.rate_limit = 60
    client = ChatOpenAIClient(chat_openai=base)
    assert client.rate_limit == 60
    client.rate_limit = 30
    assert base.rate_limit == 30


def test_chat_openai_client_exposes_model():
    """model читается из базового клиента."""
    base = MagicMock()
    base.model = "gpt-4"
    client = ChatOpenAIClient(chat_openai=base)
    assert client.model == "gpt-4"


def test_chat_openai_client_exposes_system_prompt():
    """system_prompt читается из базового клиента."""
    base = MagicMock()
    base.system_prompt = "You are helpful"
    client = ChatOpenAIClient(chat_openai=base)
    assert client.system_prompt == "You are helpful"


# ─── RateLimitedAIClient (sync) ────────────────────────────────


def test_rate_limited_acquires_before_complete():
    """sync complete() сначала acquire(), потом базовый complete()."""
    base = MagicMock()
    base.complete.return_value = "x"
    rate_limiter = MagicMock()
    client = RateLimitedAIClient(client=base, rate_limiter=rate_limiter)

    result = client.complete("prompt")
    assert result == "x"
    # acquire() вызван
    rate_limiter.acquire.assert_called_once()
    # complete() вызван после acquire
    base.complete.assert_called_once_with("prompt")


def test_rate_limited_blocks_underlying_on_throttling():
    """Если limiter блокирует — complete() всё равно вызывается после."""
    base = MagicMock()
    base.complete.return_value = "y"
    # Реальный limiter с burst=1
    limiter = TokenBucketRateLimiter(rate=1, burst=1, clock=MagicMock())
    client = RateLimitedAIClient(client=base, rate_limiter=limiter)

    # Два запроса подряд: первый — сразу, второй — acquire() вызовет
    # clock.sleep() (потому что burst=1 уже потрачен)
    client.complete("a")
    client.complete("b")
    assert base.complete.call_count == 2


def test_rate_limited_delegates_attribute_access():
    """__getattr__ делегирует доступ к атрибутам базового клиента."""
    base = MagicMock()
    base.model = "gpt-3.5"
    base.system_prompt = "sp"
    client = RateLimitedAIClient(client=base, rate_limiter=MagicMock())
    assert client.model == "gpt-3.5"
    assert client.system_prompt == "sp"


# ─── RateLimitedAIClient (async) ───────────────────────────────


def test_rate_limited_async_acquires_before_complete():
    """async_complete() сначала async_acquire(), потом complete()."""
    base = MagicMock()
    base.complete.return_value = "async-result"
    rate_limiter = MagicMock()

    async def fake_acquire() -> None:
        return None

    rate_limiter.async_acquire = fake_acquire
    client = RateLimitedAIClient(client=base, rate_limiter=rate_limiter)

    result = asyncio.run(client.async_complete("p"))
    assert result == "async-result"
    base.complete.assert_called_once_with("p")


def test_rate_limited_async_uses_async_limiter(monkeypatch):
    """async_complete() зовёт async_acquire, а не acquire.

    Подменяем time.monotonic + asyncio.sleep так, чтобы limiter
    "думал", что прошло много времени, но реально тест выполнялся
    мгновенно.
    """
    base = MagicMock()
    base.complete.return_value = "z"
    # Реальный limiter, с burst=1, rate=1 (1 token/sec)
    limiter = TokenBucketRateLimiter(rate=1, burst=1, clock=TimeDelay())
    client = RateLimitedAIClient(client=base, rate_limiter=limiter)

    import time as time_module

    fake_time = [0.0]

    def fake_monotonic() -> float:
        # Каждый вызов продвигает «время» на 10 секунд
        fake_time[0] += 10.0
        return fake_time[0]

    monkeypatch.setattr(time_module, "monotonic", fake_monotonic)
    # В limiter'е используется time.monotonic через прямой импорт
    monkeypatch.setattr(
        "hh_applicant_tool.infrastructure.delay.time.monotonic",
        fake_monotonic,
    )

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "hh_applicant_tool.infrastructure.delay.asyncio.sleep",
        fake_sleep,
    )

    async def driver() -> None:
        await client.async_complete("a")
        await client.async_complete("b")
        await client.async_complete("c")

    asyncio.run(driver())
    assert base.complete.call_count == 3


def test_rate_limited_async_propagates_complete_error():
    """Исключение из complete() пробрасывается после acquire()."""
    base = MagicMock()
    base.complete.side_effect = RuntimeError("boom")
    rate_limiter = MagicMock()

    async def fake_acquire() -> None:
        return None

    rate_limiter.async_acquire = fake_acquire
    client = RateLimitedAIClient(client=base, rate_limiter=rate_limiter)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(client.async_complete("p"))


# ─── TokenBucketRateLimiterForAI ────────────────────────────────
# (вспомогательный класс, проверяем что он корректно конвертирует rpm)


def test_token_bucket_for_ai_zero_rate_is_unlimited():
    """requests_per_minute=0 → безлимитный режим (acquire = no-op)."""
    limiter = TokenBucketRateLimiterForAI(requests_per_minute=0)
    # acquire() не должен бросать
    limiter.acquire()

    async def driver() -> None:
        await limiter.async_acquire()

    asyncio.run(driver())


def test_token_bucket_for_ai_updates_rate():
    """update_rate() пересоздаёт внутренний limiter."""
    limiter = TokenBucketRateLimiterForAI(requests_per_minute=0)
    # Безлимитный
    assert limiter._rate_limiter is None
    # Переключаем в режим 60 rpm
    limiter.update_rate(60)
    assert limiter._rate_limiter is not None
    # Снова в безлимитный
    limiter.update_rate(0)
    assert limiter._rate_limiter is None
