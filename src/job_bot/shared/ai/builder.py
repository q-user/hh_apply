"""AI client infrastructure implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from job_bot.shared.ai._chat_openai import ChatOpenAI
    from job_bot.shared.ports import (
        AIClientPort,
        RateLimiterPort,
    )

from job_bot.shared.utils.delay import TimeDelay, TokenBucketRateLimiter

__all__ = ["ChatOpenAIClient", "RateLimitedAIClient", "ChatOpenAI"]


class ChatOpenAIClient:
    """Wrapper around existing ai.ChatOpenAI implementing AIClientPort.

    This provides a clean port interface over the existing implementation.
    """

    def __init__(self, chat_openai: "ChatOpenAI") -> None:
        """Initialize with existing ChatOpenAI instance.

        Args:
            chat_openai: Existing ChatOpenAI instance from job_bot.shared.ai.
        """
        self._chat_openai = chat_openai

    def complete(self, prompt: str) -> str:
        """Generate completion for a prompt.

        Args:
            prompt: Input prompt.

        Returns:
            Generated text.
        """
        return self._chat_openai.complete(prompt)

    @property
    def rate_limit(self) -> int:
        """Get current rate limit (requests per minute)."""
        return self._chat_openai.rate_limit

    @rate_limit.setter
    def rate_limit(self, value: int) -> None:
        """Set rate limit (requests per minute)."""
        self._chat_openai.rate_limit = value

    @property
    def model(self) -> str | None:
        """Get model name."""
        return self._chat_openai.model

    @property
    def system_prompt(self) -> str | None:
        """Get system prompt."""
        return self._chat_openai.system_prompt


class RateLimitedAIClient:
    """Decorator adding rate limiting to any AIClientPort implementation.

    Uses token bucket algorithm for smooth rate limiting.
    """

    def __init__(
        self,
        client: "AIClientPort",
        rate_limiter: "RateLimiterPort",
    ) -> None:
        """Initialize rate-limited AI client.

        Args:
            client: Underlying AI client to wrap.
            rate_limiter: Rate limiter to use for acquiring permits.
        """
        self._client = client
        self._rate_limiter = rate_limiter

    def complete(self, prompt: str) -> str:
        """Generate completion with rate limiting.

        Args:
            prompt: Input prompt.

        Returns:
            Generated text.
        """
        self._rate_limiter.acquire()
        return self._client.complete(prompt)

    async def async_complete(self, prompt: str) -> str:
        """Generate completion with rate limiting (async).

        Args:
            prompt: Input prompt.

        Returns:
            Generated text.
        """
        await self._rate_limiter.async_acquire()
        return self._client.complete(prompt)

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to wrapped client."""
        return getattr(self._client, name)


class TokenBucketRateLimiterForAI:
    """Token bucket rate limiter specifically for AI clients.

    Compatible with the existing ChatOpenAI rate_limit parameter
    (requests per minute).
    """

    def __init__(self, requests_per_minute: int) -> None:
        """Initialize rate limiter.

        Args:
            requests_per_minute: Maximum requests per minute (0 = unlimited).
        """
        if requests_per_minute <= 0:
            self._rate_limiter = None
        else:
            # Convert to requests per second
            rate_per_second = requests_per_minute / 60.0
            self._rate_limiter = TokenBucketRateLimiter(
                rate=rate_per_second,
                burst=min(requests_per_minute, 10),
                clock=TimeDelay(),
            )

    def acquire(self) -> None:
        """Acquire permission to proceed (blocking)."""
        if self._rate_limiter:
            self._rate_limiter.acquire()

    async def async_acquire(self) -> None:
        """Acquire permission to proceed (async)."""
        if self._rate_limiter:
            await self._rate_limiter.async_acquire()

    def update_rate(self, requests_per_minute: int) -> None:
        """Update rate limit dynamically.

        Args:
            requests_per_minute: New rate limit (0 = unlimited).
        """
        if requests_per_minute <= 0:
            self._rate_limiter = None
        else:
            rate_per_second = requests_per_minute / 60.0
            self._rate_limiter = TokenBucketRateLimiter(
                rate=rate_per_second,
                burst=min(requests_per_minute, 10),
                clock=TimeDelay(),
            )
