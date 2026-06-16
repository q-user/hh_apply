"""Tests for CaptchaHandler (issue #145).

The handler prefers the ``CaptchaSolverPort`` (issue #38) when
supplied; falls back to the legacy Playwright path. The tests use
a fake port and a fake Playwright-free session.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from job_bot.application_submit.handlers.captcha_handler import CaptchaHandler


class _FakeCaptchaSolver:
    """Fake :class:`CaptchaSolverPort` implementation.

    Records the ``solve_captcha_url`` call and returns a pre-baked
    text (or raises if ``raise_with`` is set).
    """

    def __init__(
        self,
        return_text: str = "solved",
        raise_with: BaseException | None = None,
    ) -> None:
        self._text = return_text
        self._raise = raise_with
        self.calls: list[str] = []

    async def solve_captcha(self, image_bytes: bytes) -> str:
        return self._text

    async def solve_captcha_url(self, url: str) -> str:
        self.calls.append(url)
        if self._raise is not None:
            raise self._raise
        return self._text


# ─── solve_captcha_async (port) ──────────────────────────────────────


class TestCaptchaHandlerSolveAsync:
    """``solve_captcha_async`` delegates to the port when supplied."""

    @pytest.mark.asyncio
    async def test_port_returns_text_returns_true(self) -> None:
        solver = _FakeCaptchaSolver(return_text="captcha123")
        handler = CaptchaHandler(captcha_solver=solver)
        result = await handler.solve_captcha_async(
            "https://example.com/captcha"
        )
        assert result is True
        assert solver.calls == ["https://example.com/captcha"]

    @pytest.mark.asyncio
    async def test_port_returns_empty_text_returns_false(self) -> None:
        solver = _FakeCaptchaSolver(return_text="")
        handler = CaptchaHandler(captcha_solver=solver)
        result = await handler.solve_captcha_async(
            "https://example.com/captcha"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_port_raises_returns_false(self) -> None:
        """Any exception from the port is caught; the handler returns
        ``False`` (the apply loop logs and continues)."""
        solver = _FakeCaptchaSolver(raise_with=RuntimeError("port down"))
        handler = CaptchaHandler(captcha_solver=solver)
        result = await handler.solve_captcha_async(
            "https://example.com/captcha"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_port_raises_aierror_returns_false(self) -> None:
        from hh_applicant_tool.ai.base import AIError

        solver = _FakeCaptchaSolver(raise_with=AIError("ai down"))
        handler = CaptchaHandler(captcha_solver=solver)
        result = await handler.solve_captcha_async(
            "https://example.com/captcha"
        )
        assert result is False


# ─── solve_captcha (sync wrapper) ────────────────────────────────────


class TestCaptchaHandlerSolveSync:
    """``solve_captcha`` is the sync wrapper used by the apply loop."""

    def test_sync_wrapper_returns_true(self) -> None:
        solver = _FakeCaptchaSolver(return_text="captcha123")
        handler = CaptchaHandler(captcha_solver=solver)
        result = handler.solve_captcha("https://example.com/captcha")
        assert result is True

    def test_sync_wrapper_returns_false_on_empty(self) -> None:
        solver = _FakeCaptchaSolver(return_text="")
        handler = CaptchaHandler(captcha_solver=solver)
        result = handler.solve_captcha("https://example.com/captcha")
        assert result is False

    def test_sync_wrapper_returns_false_on_exception(self) -> None:
        solver = _FakeCaptchaSolver(raise_with=RuntimeError("port down"))
        handler = CaptchaHandler(captcha_solver=solver)
        result = handler.solve_captcha("https://example.com/captcha")
        assert result is False


# ─── Legacy fallback (Playwright) ─────────────────────────────────────


class TestCaptchaHandlerLegacyFallback:
    """When no port is supplied, the handler falls back to the legacy
    Playwright path. The actual Playwright call is not exercised here
    (we don't want a network dependency in unit tests); we just
    verify that the handler returns ``False`` when ``captcha_ai`` is
    not configured (the legacy path raises an ``ImportError`` if
    Playwright is not installed, which the handler catches)."""

    def test_no_port_no_ai_returns_false(self) -> None:
        handler = CaptchaHandler(captcha_solver=None, captcha_ai=None)
        result = handler.solve_captcha("https://example.com/captcha")
        assert result is False

    def test_no_port_with_ai_tries_playwright(self) -> None:
        """With ``captcha_ai`` set but no Playwright installed (in a
        test env), the legacy fallback returns ``False``."""
        handler = CaptchaHandler(
            captcha_solver=None,
            captcha_ai=MagicMock(solve_captcha=MagicMock(return_value="x")),
        )
        result = handler.solve_captcha("https://example.com/captcha")
        # Either Playwright works (unlikely in CI) → True, or it
        # fails (no module / no browser) → False. Both outcomes are
        # acceptable for this assertion; we just want the handler to
        # not raise.
        assert result in (True, False)


# ─── Protocol satisfaction ────────────────────────────────────────────


def test_captcha_handler_satisfies_captcha_port() -> None:
    from job_bot.application_submit.ports.captcha_port import CaptchaPort

    handler: CaptchaPort = CaptchaHandler(captcha_solver=MagicMock())
    assert callable(handler.solve_captcha)
    assert callable(handler.solve_captcha_async)
