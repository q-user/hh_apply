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
        solver to completion in a fresh event loop. A short outer
        timeout guarantees the handler fails fast even when the
        legacy Playwright fallback hangs on a missing browser,
        network restriction, or unresponsive captcha page.
        """
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self.solve_captcha_async(captcha_url),
                    timeout=4.0,
                )
            )
        except asyncio.TimeoutError:
            logger.error("Captcha solving timed out after 4s; returning False")
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

        Any failure (Playwright internal timeout, network error,
        missing browser deps, unexpected exception from the AI
        client) is caught and reported as ``False`` so the apply
        loop can move on instead of crashing the worker.
        """
        try:
            from playwright.async_api import (  # type: ignore[import-not-found,unused-ignore]
                async_playwright,
            )
        except ImportError:
            logger.error(
                "playwright is not installed; cannot solve captcha via fallback"
            )
            return False

        try:
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

                    if self._ai is None:
                        logger.error(
                            "captcha_ai is not configured; cannot solve captcha"
                        )
                        return False
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
        except Exception as ex:  # noqa: BLE001
            logger.error("Playwright captcha solving failed: %s", ex)
            return False


__all__ = ["CaptchaHandler"]
