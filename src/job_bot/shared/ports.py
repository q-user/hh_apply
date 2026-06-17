"""Application-layer port Protocols (issue #158).

These Protocol definitions were previously kept in
``hh_applicant_tool.application.ports``. After the VSA migration
(issue #158), the live use cases that consumed them have been
replaced by VSA slices; the Protocol classes are preserved here so
``isinstance`` / structural-typing call sites in the slice handlers
keep type-checking.

The VSA slices define their own concrete ``*Port`` aliases per-slice
(e.g. ``job_bot.application_submit.ports.captcha_port.CaptchaPort``,
``job_bot.application_submit.ports.email_port.EmailPort``); this
module re-exports the legacy wire-compatible Protocols so
``from job_bot.shared.ports import CaptchaSolverPort`` keeps working.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

import requests


class CaptchaSolverPort(Protocol):
    """Port for solving CAPTCHAs (issue #158)."""

    async def solve_captcha(self, image_bytes: bytes) -> str:
        """Solve CAPTCHA from image bytes."""
        ...

    async def solve_captcha_url(self, url: str) -> str:
        """Solve CAPTCHA by navigating to URL."""
        ...


class SiteParserPort(Protocol):
    """Port for parsing employer/site pages (issue #158)."""

    def parse_site(self, url: str) -> dict[str, Any]:
        """Parse a site URL and extract metadata."""
        ...


class EmailSenderPort(Protocol):
    """Port for sending emails (issue #158)."""

    def send_email(self, to: str, subject: str, body: str) -> None:
        """Send an email."""
        ...


class DelayPort(Protocol):
    """Port for sleeping/delaying execution (issue #158)."""

    def sleep(self, seconds: float) -> None:
        """Sleep for specified seconds."""
        ...


class RateLimiterPort(Protocol):
    """Port for rate limiting (issue #158)."""

    def acquire(self) -> None:
        """Acquire permission to proceed (blocking)."""
        ...

    async def async_acquire(self) -> None:
        """Acquire permission to proceed (async)."""
        ...


class HttpClientPort(Protocol):
    """Port for HTTP requests (issue #158)."""

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """Perform GET request."""
        ...

    def post(
        self, url: str, data: dict[str, Any], **kwargs: Any
    ) -> requests.Response:
        """Perform POST request."""
        ...


class CancellationToken(Protocol):
    """Port for cancellation token (issue #158)."""

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation was requested."""
        ...

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called on cancellation."""
        ...


class Clock(Protocol):
    """Port for time operations (issue #158)."""

    def now(self) -> datetime:
        """Get current datetime."""
        ...

    def sleep(self, seconds: float) -> None:
        """Sleep for specified seconds."""
        ...


class VacancyDescriptionFetcherPort(Protocol):
    """Port for fetching vacancy descriptions (issue #158)."""

    def fetch(self, vacancy_id: str) -> dict[str, Any] | None:
        """Fetch full vacancy description by ID."""
        ...


class TestVacancyLoggerPort(Protocol):
    """Port for logging vacancies with tests (issue #158)."""

    def log(
        self, vacancy_name: str, employer_name: str, test_link: str
    ) -> None:
        """Log a vacancy that has a test."""
        ...


class AIClientPort(Protocol):
    """Port for AI text completion (issue #158)."""

    def complete(self, prompt: str) -> str:
        """Generate completion for a prompt."""
        ...


__all__ = [
    "AIClientPort",
    "CancellationToken",
    "CaptchaSolverPort",
    "Clock",
    "DelayPort",
    "EmailSenderPort",
    "HttpClientPort",
    "RateLimiterPort",
    "SiteParserPort",
    "TestVacancyLoggerPort",
    "VacancyDescriptionFetcherPort",
]
