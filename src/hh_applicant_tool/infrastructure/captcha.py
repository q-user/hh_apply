"""CAPTCHA solver infrastructure implementations."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__package__)


class PlaywrightCaptchaSolver:
    """CAPTCHA solver using Playwright with browser pooling.

    Reuses browser instances for better performance.
    """

    def __init__(
        self,
        ai_client: Any,
        *,
        headless: bool = True,
        timeout: float = 30.0,
        max_retries: int = 3,
        browser_pool_size: int = 1,
        captcha_image_selector: str = 'img[data-qa="account-captcha-picture"]',
        captcha_input_selector: str = 'input[data-qa="account-captcha-input"]',
    ) -> None:
        """Initialize CAPTCHA solver.

        Args:
            ai_client: AI client implementing AIClientPort for image recognition.
            headless: Run browser in headless mode.
            timeout: Page navigation timeout in seconds.
            max_retries: Maximum retries for solving.
            browser_pool_size: Number of browser instances to pool.
            captcha_image_selector: CSS selector for CAPTCHA image.
            captcha_input_selector: CSS selector for CAPTCHA input field.
        """
        self._ai_client = ai_client
        self._headless = headless
        self._timeout = timeout * 1000  # Convert to ms for Playwright
        self._max_retries = max_retries
        self._browser_pool_size = max(1, browser_pool_size)
        self._captcha_image_selector = captcha_image_selector
        self._captcha_input_selector = captcha_input_selector

        self._browser_pool: list[Any] = []
        self._pool_lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Initialize browser pool if not already done."""
        if self._initialized:
            return

        async with self._pool_lock:
            if self._initialized:
                return

            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()

                for _ in range(self._browser_pool_size):
                    browser = await self._playwright.chromium.launch(
                        headless=self._headless
                    )
                    self._browser_pool.append(browser)

                self._initialized = True
                logger.debug(
                    "Initialized browser pool with %d instances",
                    self._browser_pool_size,
                )
            except Exception as ex:
                logger.error("Failed to initialize browser pool: %s", ex)
                raise

    @asynccontextmanager
    async def _get_browser(self):
        """Get a browser from the pool."""
        await self._ensure_initialized()

        async with self._pool_lock:
            if not self._browser_pool:
                # Pool exhausted, create new browser
                browser = await self._playwright.chromium.launch(
                    headless=self._headless
                )
            else:
                browser = self._browser_pool.pop()

        try:
            yield browser
        finally:
            async with self._pool_lock:
                if len(self._browser_pool) < self._browser_pool_size:
                    self._browser_pool.append(browser)
                else:
                    # Pool full, close this browser
                    await browser.close()

    async def solve_captcha(self, image_bytes: bytes) -> str:
        """Solve CAPTCHA from image bytes.

        Args:
            image_bytes: Raw image data (PNG/JPEG).

        Returns:
            Recognized text from CAPTCHA.
        """
        return await self._ai_client.complete(
            f"Распознай текст на изображении капчи. "
            f"Изображение: {len(image_bytes)} bytes. "
            f"Верни только результат распознавания."
        )

    async def solve_captcha_url(self, url: str) -> str:
        """Solve CAPTCHA by navigating to URL.

        Args:
            url: CAPTCHA page URL.

        Returns:
            Recognized text from CAPTCHA.
        """
        await self._ensure_initialized()

        for attempt in range(self._max_retries):
            try:
                async with self._get_browser() as browser:
                    context = await browser.new_context()
                    page = await context.new_page()

                    try:
                        await page.goto(url, timeout=self._timeout)

                        captcha_element = await page.wait_for_selector(
                            self._captcha_image_selector,
                            timeout=10000,
                            state="visible",
                        )

                        await captcha_element.screenshot()

                        captcha_text = await self._ai_client.complete(
                            "Распознай текст на изображении капчи. "
                            "Верни только результат распознавания."
                        )

                        if not captcha_text:
                            logger.warning("AI returned empty CAPTCHA text")
                            continue

                        logger.info("Recognized CAPTCHA text: %s", captcha_text)

                        await page.fill(
                            self._captcha_input_selector, captcha_text
                        )
                        await page.press(self._captcha_input_selector, "Enter")

                        await page.wait_for_load_state(
                            "networkidle", timeout=15000
                        )

                        return captcha_text

                    finally:
                        await context.close()

            except Exception as ex:
                logger.warning(
                    "CAPTCHA solve attempt %d/%d failed: %s",
                    attempt + 1,
                    self._max_retries,
                    ex,
                )
                if attempt == self._max_retries - 1:
                    raise

        raise RuntimeError("Failed to solve CAPTCHA after all retries")

    async def close(self) -> None:
        """Close all browsers in the pool."""
        async with self._pool_lock:
            for browser in self._browser_pool:
                try:
                    await browser.close()
                except Exception:
                    pass
            self._browser_pool.clear()

            if hasattr(self, "_playwright"):
                try:
                    await self._playwright.stop()
                except Exception:
                    pass

    async def __aenter__(self) -> "PlaywrightCaptchaSolver":
        await self._ensure_initialized()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
