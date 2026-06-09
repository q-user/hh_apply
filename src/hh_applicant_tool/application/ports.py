"""Port abstractions (Protocols) for infrastructure dependencies.

This module defines all port interfaces that use cases depend on.
Implementations live in ``src/hh_applicant_tool/infrastructure/``.

Using Protocol classes enables structural subtyping - implementations
don't need to explicitly inherit from these classes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

import requests


class CaptchaSolverPort(Protocol):
    """Port for solving CAPTCHAs."""

    async def solve_captcha(self, image_bytes: bytes) -> str:
        """Solve CAPTCHA from image bytes.

        Args:
            image_bytes: Raw image data (PNG/JPEG).

        Returns:
            Recognized text from CAPTCHA.
        """
        ...

    async def solve_captcha_url(self, url: str) -> str:
        """Solve CAPTCHA by navigating to URL.

        Args:
            url: CAPTCHA page URL.

        Returns:
            Recognized text from CAPTCHA.
        """
        ...


class SiteParserPort(Protocol):
    """Port for parsing employer/site pages."""

    def parse_site(self, url: str) -> dict[str, Any]:
        """Parse a site URL and extract metadata.

        Args:
            url: Site URL to parse.

        Returns:
            Dictionary with keys: title, description, generator, emails,
            server_name, powered_by, ip_address.
        """
        ...


class EmailSenderPort(Protocol):
    """Port for sending emails."""

    def send_email(self, to: str, subject: str, body: str) -> None:
        """Send an email.

        Args:
            to: Recipient email address.
            subject: Email subject.
            body: Email body text.
        """
        ...


class DelayPort(Protocol):
    """Port for sleeping/delaying execution."""

    def sleep(self, seconds: float) -> None:
        """Sleep for specified seconds.

        Args:
            seconds: Number of seconds to sleep.
        """
        ...


class RateLimiterPort(Protocol):
    """Port for rate limiting."""

    def acquire(self) -> None:
        """Acquire permission to proceed (blocking)."""
        ...

    async def async_acquire(self) -> None:
        """Acquire permission to proceed (async)."""
        ...


class HttpClientPort(Protocol):
    """Port for HTTP requests."""

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """Perform GET request.

        Args:
            url: Request URL.
            **kwargs: Additional arguments passed to requests.

        Returns:
            Response object.
        """
        ...

    def post(
        self, url: str, data: dict[str, Any], **kwargs: Any
    ) -> requests.Response:
        """Perform POST request.

        Args:
            url: Request URL.
            data: Request body data.
            **kwargs: Additional arguments passed to requests.

        Returns:
            Response object.
        """
        ...


class CancellationToken(Protocol):
    """Port for cancellation token."""

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation was requested."""
        ...

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called on cancellation.

        Args:
            callback: Function to call when cancelled.
        """
        ...


class Clock(Protocol):
    """Port for time operations."""

    def now(self) -> datetime:
        """Get current datetime."""
        ...

    def sleep(self, seconds: float) -> None:
        """Sleep for specified seconds.

        Args:
            seconds: Number of seconds to sleep.
        """
        ...


class VacancyDescriptionFetcherPort(Protocol):
    """Port for fetching vacancy descriptions."""

    def fetch(self, vacancy_id: str) -> dict[str, Any] | None:
        """Fetch full vacancy description by ID.

        Args:
            vacancy_id: Vacancy identifier.

        Returns:
            Vacancy data dict or None if not found.
        """
        ...


class TestVacancyLoggerPort(Protocol):
    """Port for logging vacancies with tests."""

    def log(
        self, vacancy_name: str, employer_name: str, test_link: str
    ) -> None:
        """Log a vacancy that has a test.

        Args:
            vacancy_name: Name of the vacancy.
            employer_name: Name of the employer.
            test_link: URL to the test.
        """
        ...


class AIClientPort(Protocol):
    """Port for AI text completion."""

    def complete(self, prompt: str) -> str:
        """Generate completion for a prompt.

        Args:
            prompt: Input prompt.

        Returns:
            Generated text.
        """
        ...
