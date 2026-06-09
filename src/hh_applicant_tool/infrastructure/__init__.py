"""Infrastructure implementations for application ports.

This module provides concrete implementations of the port interfaces
defined in ``hh_applicant_tool.application.ports``.

All implementations are designed to be injected via dependency injection
(through ``container.py``) into use cases and services.
"""

from .ai import ChatOpenAIClient, RateLimitedAIClient
from .captcha import PlaywrightCaptchaSolver
from .delay import AsyncDelay, RandomDelay, TimeDelay, TokenBucketRateLimiter
from .email import SMTPEmailSender
from .http import RequestsHttpClient, RequestsSiteParser
from .test_logger import FileTestVacancyLogger
from .time import (
    AsyncioCancellationToken,
    SystemClock,
    ThreadingCancellationToken,
)
from .vacancy_fetcher import CachedVacancyDescriptionFetcher

__all__ = [
    # Time & cancellation
    "SystemClock",
    "ThreadingCancellationToken",
    "AsyncioCancellationToken",
    # HTTP
    "RequestsSiteParser",
    "RequestsHttpClient",
    # Delay & rate limiting
    "TimeDelay",
    "AsyncDelay",
    "TokenBucketRateLimiter",
    "RandomDelay",
    # Email
    "SMTPEmailSender",
    # Captcha
    "PlaywrightCaptchaSolver",
    # Vacancy fetcher
    "CachedVacancyDescriptionFetcher",
    # Test logger
    "FileTestVacancyLogger",
    # AI
    "ChatOpenAIClient",
    "RateLimitedAIClient",
]
