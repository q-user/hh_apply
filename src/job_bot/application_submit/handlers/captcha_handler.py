"""CaptchaHandler -- CAPTCHA solving (issue #145).

In-slice VSA wrapper for the legacy
``ApplyToVacanciesUseCase._solve_captcha_async`` helper. Prefers the
``CaptchaSolverPort`` (issue #38) when supplied; falls back to the
legacy Playwright path.

The apply pipeline is sync, so the handler exposes both
:meth:`solve_captcha` (sync, runs the async coroutine via
``asyncio.run``) and :meth:`solve_captcha_async` (raw async, used
by tests and by external async callers).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from job_bot.shared.ports import CaptchaSolverPort

logger = logging.getLogger(__package__)

# Playwright's exception classes live in a private module; fall back to
# ``Exception`` only when Playwright isn't installed so the handler can
# still be imported in minimal environments (issue #210).
try:
    from playwright._impl._errors import Error as PlaywrightError
except ImportError:  # pragma: no cover - exercised only without playwright
    PlaywrightError = Exception  # type: ignore[assignment,misc]


class CaptchaHandler:
    """In-slice captcha handler (issue #145).

    Args:
        captcha_solver: optional :class:`CaptchaSolverPort` (issue #38).
        captcha_ai: legacy AI client used by the Playwright fallback
            (``captcha_ai.solve_captcha(image_bytes) -> str``).
        session: legacy ``requests.Session`` whose cookies are populated
            by the Playwright fallback after a successful solve.
    """

    SEL_CAPTCHA_IMAGE = 'img[data-qa="account-captcha-picture"]'
    SEL_CAPTCHA_INPUT = 'input[data-qa="account-captcha-input"]'

    # Per-call Playwright timeout (issue #204). The legacy
    # ``_solve_with_playwright`` path needs to bound both sync and
    # async callers (the sync wrapper's own outer guard is wider
    # defense-in-depth). 30s is the reviewer's suggested value: long
    # enough to absorb the 30s page.goto + 10s wait_for_selector +
    # 15s wait_for_load_state (55s theoretical worst case) and the
    # AI solve, short enough to fail fast on a true Playwright hang
    # (wedged event loop, stuck socket). A typical real solve is
    # well under 5s, so 30s is mostly slack.
    _PLAYWRIGHT_TIMEOUT_S: float = 30.0

    # Defense-in-depth outer guard for the sync wrapper. Wider than
    # the inner ``_PLAYWRIGHT_TIMEOUT_S`` so a hang in the inner
    # ``wait_for`` itself (e.g. cancellation gone wrong) is still
    # bounded. The per-call timeout in ``_solve_with_playwright``
    # catches the normal hang case first.
    _SYNC_WRAPPER_TIMEOUT_S: float = 35.0

    def __init__(
        self,
        captcha_solver: "CaptchaSolverPort | None" = None,
        *,
        captcha_ai: Any = None,
        session: Any = None,
    ) -> None:
        self._solver = captcha_solver
        self._ai = captcha_ai
        self._session = session

    # ─── Public API ────────────────────────────────────────────

    def solve_captcha(self, captcha_url: str) -> bool:
        """Sync wrapper over :meth:`solve_captcha_async`.

        The apply pipeline is sync; this entry point runs the async
        solver to completion in a fresh event loop. A defense-in-depth
        outer timeout guarantees the handler fails fast even when the
        legacy Playwright fallback hangs on a missing browser, network
        restriction, or unresponsive captcha page. The primary
        ``asyncio.wait_for`` lives in
        :meth:`_solve_with_playwright` so the same protection covers
        the direct async caller
        (``ApplyToVacanciesUseCase._solve_captcha_async``).
        """
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self.solve_captcha_async(captcha_url),
                    timeout=self._SYNC_WRAPPER_TIMEOUT_S,
                )
            )
        except asyncio.TimeoutError:
            logger.error(
                "Captcha solving timed out after %ss; returning False",
                self._SYNC_WRAPPER_TIMEOUT_S,
            )
            return False

    async def solve_captcha_async(self, captcha_url: str) -> bool:
        """Solve the CAPTCHA at ``captcha_url`` and return ``True`` on success.

        Prefers the :class:`CaptchaSolverPort` when supplied. Falls
        back to the legacy Playwright path (``captcha_ai`` +
        ``session``).
        """
        if self._solver is not None:
            try:
                text = await self._solver.solve_captcha_url(captcha_url)
                if text:
                    logger.info("CaptchaSolverPort solved: %s", text)
                    return True
                logger.error("CaptchaSolverPort returned empty text")
                return False
            except Exception as ex:  # noqa: BLE001
                logger.error("CaptchaSolverPort failed: %s", ex)
                return False

        # Legacy fallback: inline Playwright.
        return await self._solve_with_playwright(captcha_url)

    # ─── Internals ─────────────────────────────────────────────

    async def _solve_with_playwright(self, captcha_url: str) -> bool:
        """Open the captcha page in a headless Chromium, OCR it via ``captcha_ai``,
        submit, and propagate the resulting cookies to ``session``.

        The whole solve is wrapped in ``asyncio.wait_for(..., timeout=
        _PLAYWRIGHT_TIMEOUT_S)`` (issue #204) so this layer protects
        both the sync wrapper AND the direct async caller
        (``ApplyToVacanciesUseCase._solve_captcha_async``) uniformly.

        Only environment-shaped failures are caught and reported as
        ``False`` so the apply loop can move on: Playwright errors
        (browser missing, page timeout, navigation failure), network
        errors (``OSError``), the ``asyncio.TimeoutError`` raised by
        the inner ``wait_for``, and ``ImportError`` (Playwright not
        installed at runtime). Programming errors (e.g. ``TypeError``,
        ``AttributeError``) are deliberately NOT swallowed so real
        bugs surface in logs and tests instead of being silently
        turned into 'captcha failed' (issue #210).
        """
        try:
            return await asyncio.wait_for(
                self._run_playwright_solve(captcha_url),
                timeout=self._PLAYWRIGHT_TIMEOUT_S,
            )
        except (
            PlaywrightError,
            OSError,
            asyncio.TimeoutError,
            ImportError,
        ) as ex:
            # ``ImportError`` is also caught by the inner import guard,
            # but listing it here keeps the contract explicit and
            # survives future refactors of the import block.
            logger.error("Playwright captcha solving failed: %s", ex)
            return False

    async def _run_playwright_solve(self, captcha_url: str) -> bool:
        """Open the captcha page in a headless Chromium, OCR it via ``captcha_ai``,
        submit, and propagate the resulting cookies to ``session``.

        Only environment-shaped failures propagate to
        :meth:`_solve_with_playwright` for handling: ``TypeError`` and
        other programming errors are deliberately NOT caught here so
        real bugs surface (issue #210). ``PlaywrightError``, ``OSError``,
        ``asyncio.TimeoutError``, and ``ImportError`` are caught by the
        caller.
        """
        # Fail fast on misconfiguration: don't pay the cost of spawning
        # a headless browser when the AI client is missing. The check
        # used to live below the launch (it still works there too),
        # but moving it up keeps unit tests fast when the browser
        # binary IS installed but the AI client isn't.
        if self._ai is None:
            logger.error("captcha_ai is not configured; cannot solve captcha")
            return False

        # Local import: ``playwright`` is an optional runtime dep, and
        # ``ImportError`` is mapped to ``False`` by the caller's
        # ``except`` clause (issue #210).
        from playwright.async_api import (  # type: ignore[import-not-found,unused-ignore]
            async_playwright,
        )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()

                await page.goto(captcha_url, timeout=30000)

                captcha_element = await page.wait_for_selector(
                    self.SEL_CAPTCHA_IMAGE, timeout=10000, state="visible"
                )
                if captcha_element is None:
                    logger.error("Captcha image element not found")
                    return False

                img_bytes = await captcha_element.screenshot()

                captcha_text = await asyncio.to_thread(
                    self._ai.solve_captcha, img_bytes
                )
                if not captcha_text:
                    logger.error("AI не смог распознать капчу")
                    return False

                logger.info(f"Распознанный текст капчи: {captcha_text}")

                await page.fill(self.SEL_CAPTCHA_INPUT, captcha_text)
                await page.press(self.SEL_CAPTCHA_INPUT, "Enter")

                await page.wait_for_load_state("networkidle", timeout=15000)

                cookies = await context.cookies()
                session = self._session
                if session is not None:
                    for c in cookies:
                        session.cookies.set(
                            c["name"],
                            c["value"],
                            domain=c.get("domain", ""),
                            path=c.get("path", "/"),
                        )

                return True
            finally:
                await browser.close()


__all__ = ["CaptchaHandler"]
