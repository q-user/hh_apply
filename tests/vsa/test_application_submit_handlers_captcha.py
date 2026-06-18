"""Tests for CaptchaHandler (issue #145).

The handler prefers the ``CaptchaSolverPort`` (issue #38) when
supplied; falls back to the legacy Playwright path. The tests use
a fake port and a fake Playwright-free session.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

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
        from job_bot.shared.ai._errors import AIError

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


# ─── Legacy fallback (Playwright) ───────────────────────────


def _playwright_browser_available() -> bool:
    """Return True iff the Playwright browser binary is installed.

    CI runners don't always install the headless-shell browser even
    when the playwright Python package is available, so we probe the
    binary path Playwright would launch rather than just importing
    the module.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            # ``chromium.launch`` raises if the browser binary is
            # missing. We don't actually launch — we just check
            # ``executable_path`` resolves.
            browser = pw.chromium.executable_path
            return bool(browser) and Path(browser).exists()
    except Exception:  # noqa: BLE001
        return False


from pathlib import Path  # noqa: E402  (used by helper above)


@pytest.mark.skipif(
    not _playwright_browser_available(),
    reason="Playwright browser binary not installed (CI env without browser)",
)
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

    def test_legacy_fallback_invokes_async_playwright(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for issue #210: the previous assertion
        ``result in (True, False)`` was tautological — any ``bool`` would
        pass. Strengthen by mocking ``async_playwright`` and asserting it
        was actually called, so we verify the Playwright path was taken
        (not an early return)."""
        # Mock the async context manager returned by async_playwright().
        # The browser launch raises a PlaywrightError subclass so the
        # solve returns False — but the call itself is the proof.
        from playwright._impl._errors import Error as PlaywrightError

        fake_pw = MagicMock()
        fake_pw.chromium.launch = AsyncMock(
            side_effect=PlaywrightError("stubbed: stop after launch")
        )
        fake_cm = MagicMock()
        fake_cm.__aenter__ = AsyncMock(return_value=fake_pw)
        fake_cm.__aexit__ = AsyncMock(return_value=None)
        fake_async_playwright = MagicMock(return_value=fake_cm)

        monkeypatch.setattr(
            "playwright.async_api.async_playwright",
            fake_async_playwright,
        )

        handler = CaptchaHandler(
            captcha_solver=None,
            captcha_ai=MagicMock(solve_captcha=MagicMock(return_value="x")),
        )
        result = handler.solve_captcha("https://example.com/captcha")

        fake_async_playwright.assert_called_once()
        assert result is False

    def test_legacy_fallback_does_not_swallow_typeerror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for issue #210: a ``TypeError`` from inside the
        Playwright path is NOT swallowed — it must propagate so real
        bugs surface instead of silently being reported as 'captcha
        failed'."""
        # TypeError is intentionally NOT a PlaywrightError / OSError /
        # asyncio.TimeoutError / ImportError; a tightened ``except``
        # must let it through.
        fake_pw = MagicMock()
        fake_pw.chromium.launch = AsyncMock(
            side_effect=TypeError("simulated programming bug")
        )
        fake_cm = MagicMock()
        fake_cm.__aenter__ = AsyncMock(return_value=fake_pw)
        fake_cm.__aexit__ = AsyncMock(return_value=None)
        fake_async_playwright = MagicMock(return_value=fake_cm)

        monkeypatch.setattr(
            "playwright.async_api.async_playwright",
            fake_async_playwright,
        )

        handler = CaptchaHandler(
            captcha_solver=None,
            captcha_ai=MagicMock(solve_captcha=MagicMock(return_value="x")),
        )
        with pytest.raises(TypeError, match="simulated programming bug"):
            handler.solve_captcha("https://example.com/captcha")


# ─── Hang protection (issue #204) ───────────────────────────


def _playwright_module_available() -> bool:
    """Return True iff the ``playwright`` Python package is importable.

    The hang tests need to patch ``playwright.async_api.async_playwright``;
    the package must be importable for the patch to apply. The handler's
    own ``ImportError`` fallback is already covered by
    ``test_no_port_no_ai_returns_false``.
    """
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


def _make_hanging_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``playwright.async_api.async_playwright`` to hang forever.

    Used to prove the handler's timeout fires from both the sync and
    async paths (issue #204).
    """

    async def _hang(*_args: object, **_kwargs: object) -> None:
        # 60s is well past any reasonable per-call timeout; the test
        # should never see this complete.
        await asyncio.sleep(60)

    fake_pw = MagicMock()
    fake_pw.chromium.launch = AsyncMock(side_effect=_hang)
    fake_cm = MagicMock()
    fake_cm.__aenter__ = AsyncMock(return_value=fake_pw)
    fake_cm.__aexit__ = AsyncMock(return_value=None)
    fake_async_playwright = MagicMock(return_value=fake_cm)
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        fake_async_playwright,
    )


# Skip the hang tests when Playwright is not importable: the test
# patches ``playwright.async_api.async_playwright`` which requires the
# package to be installed. The handler's ImportError-fallback path is
# already covered by ``test_no_port_no_ai_returns_false``.
playwright_required = pytest.mark.skipif(
    _playwright_module_available() is False,
    reason="playwright Python package not installed",
)


@playwright_required
@pytest.mark.timeout(45)
@pytest.mark.asyncio
async def test_async_path_times_out_when_playwright_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #204: ``solve_captcha_async`` must fail fast on Playwright hang.

    Before #204, the outer ``asyncio.wait_for(..., timeout=4.0)`` lived
    in the sync wrapper, so the direct async caller
    (``ApplyToVacanciesUseCase._solve_captcha_async``) was unguarded.
    """
    _make_hanging_playwright(monkeypatch)
    handler = CaptchaHandler(
        captcha_solver=None,
        captcha_ai=MagicMock(solve_captcha=MagicMock(return_value="x")),
    )
    start = time.monotonic()
    result = await handler.solve_captcha_async("https://example.com/captcha")
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed < 32.0, f"async path took {elapsed:.1f}s; expected ~30s"


@playwright_required
@pytest.mark.timeout(45)
def test_sync_path_times_out_when_playwright_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #204 regression: sync wrapper also fails fast on Playwright hang.

    After the fix, the per-call timeout lives in
    ``_solve_with_playwright``; the sync wrapper keeps a defense-in-depth
    outer guard (>30s) so the test passes within ~32s regardless of
    which layer fires first.
    """
    _make_hanging_playwright(monkeypatch)
    handler = CaptchaHandler(
        captcha_solver=None,
        captcha_ai=MagicMock(solve_captcha=MagicMock(return_value="x")),
    )
    start = time.monotonic()
    result = handler.solve_captcha("https://example.com/captcha")
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed < 32.0, f"sync path took {elapsed:.1f}s; expected ~30s"


# ─── Protocol satisfaction ────────────────────────────────────────────


def test_captcha_handler_satisfies_captcha_port() -> None:
    from job_bot.application_submit.ports.captcha_port import CaptchaPort

    handler: CaptchaPort = CaptchaHandler(captcha_solver=MagicMock())
    assert callable(handler.solve_captcha)
    assert callable(handler.solve_captcha_async)
