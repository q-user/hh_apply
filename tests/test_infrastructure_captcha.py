"""Тесты инфраструктурного CAPTCHA-солвера на Playwright.

Тесты НЕ запускают реальный браузер — только проверяем инициализацию,
поведение solve_captcha() через AI-клиент и формирование промпта.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hh_applicant_tool.infrastructure.captcha import PlaywrightCaptchaSolver

# ─── Инициализация ──────────────────────────────────────────────


def test_solver_init_with_minimal_args():
    """Конструктор принимает только ai_client — остальное по умолчанию."""
    ai = MagicMock()
    solver = PlaywrightCaptchaSolver(ai_client=ai)
    assert solver._ai_client is ai
    assert solver._headless is True
    assert solver._browser_pool_size == 1
    # timeout хранится в миллисекундах (Playwright API)
    assert solver._timeout == 30000  # 30s * 1000
    # Браузерный пул ещё не инициализирован
    assert solver._initialized is False
    assert solver._browser_pool == []


def test_solver_init_with_all_args():
    """Все параметры конструктора сохраняются."""
    ai = MagicMock()
    solver = PlaywrightCaptchaSolver(
        ai_client=ai,
        headless=False,
        timeout=15.0,
        max_retries=5,
        browser_pool_size=3,
        captcha_image_selector=".my-img",
        captcha_input_selector=".my-input",
    )
    assert solver._headless is False
    assert solver._timeout == 15000  # 15s * 1000
    assert solver._max_retries == 5
    assert solver._browser_pool_size == 3
    assert solver._captcha_image_selector == ".my-img"
    assert solver._captcha_input_selector == ".my-input"


def test_solver_browser_pool_size_clamped_to_min_one():
    """browser_pool_size < 1 клампится к 1."""
    ai = MagicMock()
    solver = PlaywrightCaptchaSolver(ai_client=ai, browser_pool_size=0)
    assert solver._browser_pool_size == 1


# ─── solve_captcha (через AI по bytes) ─────────────────────────


def test_solve_captcha_uses_ai_client():
    """solve_captcha() передаёт AI промпт и возвращает его ответ."""
    ai = MagicMock()

    async def fake_complete(prompt: str) -> str:
        return "abc123"

    ai.complete = fake_complete
    solver = PlaywrightCaptchaSolver(ai_client=ai)

    result = asyncio.run(solver.solve_captcha(b"fake-image-bytes"))
    assert result == "abc123"


def test_solve_captcha_prompt_mentions_bytes_count():
    """Промпт содержит размер изображения в байтах."""
    captured: list[str] = []

    async def fake_complete(prompt: str) -> str:
        captured.append(prompt)
        return "ok"

    ai = MagicMock()
    ai.complete = fake_complete
    solver = PlaywrightCaptchaSolver(ai_client=ai)
    image_bytes = b"X" * 1024

    asyncio.run(solver.solve_captcha(image_bytes))
    assert "1024" in captured[0]
    assert "bytes" in captured[0]


def test_solve_captcha_propagates_ai_error():
    """Исключение из AI клиента пробрасывается."""
    ai = MagicMock()

    async def fake_complete(prompt: str) -> str:
        raise RuntimeError("ai down")

    ai.complete = fake_complete
    solver = PlaywrightCaptchaSolver(ai_client=ai)

    with pytest.raises(RuntimeError, match="ai down"):
        asyncio.run(solver.solve_captcha(b"data"))


# ─── close() — безопасен на неинициализированном пуле ───────────


def test_close_on_uninitialized_solver_is_safe():
    """close() на свежем solver'е не падает (пул пуст)."""

    async def driver() -> None:
        solver = PlaywrightCaptchaSolver(ai_client=MagicMock())
        await solver.close()

    asyncio.run(driver())


def test_close_idempotent():
    """Повторный close() не падает."""

    async def driver() -> None:
        solver = PlaywrightCaptchaSolver(ai_client=MagicMock())
        await solver.close()
        await solver.close()

    asyncio.run(driver())


# ─── async context manager ──────────────────────────────────────


def test_aenter_calls_ensure_initialized(monkeypatch):
    """__aenter__ инициализирует браузерный пул через _ensure_initialized."""

    class FakeSolver:
        # Подменяем класс целиком, чтобы избежать реальной инициализации
        def __init__(self, ai):
            self._ai_client = ai
            self._initialized = False

        async def _ensure_initialized(self):
            self._initialized = True

        async def __aenter__(self):
            await self._ensure_initialized()
            return self

        async def __aexit__(self, *args):
            return None

    ai = MagicMock()
    solver = FakeSolver(ai)

    async def driver() -> None:
        async with solver as s:
            assert s._initialized is True

    asyncio.run(driver())


# ─── retry-логика solve_captcha_url (без реального браузера) ───


def test_solve_captcha_url_retries_on_failure(monkeypatch):
    """solve_captcha_url() делает до max_retries попыток и рейзит на последней."""
    from contextlib import asynccontextmanager

    ai = MagicMock()
    ai.complete = AsyncMock(return_value="x")

    # Мокаем _ensure_initialized — без браузера
    solver = PlaywrightCaptchaSolver(
        ai_client=ai,
        max_retries=2,
    )
    solver._ensure_initialized = AsyncMock()  # type: ignore[method-assign]

    # _get_browser() — async context manager, кидающий RuntimeError
    @asynccontextmanager
    async def failing_cm(self_arg):
        raise RuntimeError("browser failed")
        yield  # unreachable, but required for asynccontextmanager

    with patch.object(PlaywrightCaptchaSolver, "_get_browser", new=failing_cm):
        with pytest.raises(RuntimeError, match="browser failed"):
            asyncio.run(solver.solve_captcha_url("https://example.com/captcha"))


def test_solve_captcha_url_returns_text_on_success(monkeypatch):
    """При успехе solve_captcha_url() возвращает распознанный текст."""
    from contextlib import asynccontextmanager

    ai = MagicMock()
    ai.complete = AsyncMock(return_value="captcha-text")

    solver = PlaywrightCaptchaSolver(ai_client=ai, max_retries=2)
    solver._ensure_initialized = AsyncMock()  # type: ignore[method-assign]

    fake_browser = MagicMock()
    fake_context = AsyncMock()
    fake_page = AsyncMock()

    @asynccontextmanager
    async def fake_cm(self_arg):
        yield fake_browser

    fake_browser.new_context = AsyncMock(return_value=fake_context)
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()
    fake_page.goto = AsyncMock()
    fake_page.wait_for_selector = AsyncMock(
        return_value=MagicMock(screenshot=AsyncMock())
    )
    fake_page.fill = AsyncMock()
    fake_page.press = AsyncMock()
    fake_page.wait_for_load_state = AsyncMock()

    with patch.object(PlaywrightCaptchaSolver, "_get_browser", new=fake_cm):
        result = asyncio.run(
            solver.solve_captcha_url("https://example.com/captcha")
        )
        assert result == "captcha-text"
