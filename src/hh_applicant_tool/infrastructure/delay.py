"""Delay and rate limiter infrastructure implementations."""

from __future__ import annotations

import asyncio
import random
import time
from threading import Lock


class TimeDelay:
    """Delay implementation using time.sleep()."""

    def sleep(self, seconds: float) -> None:
        """Sleep for specified seconds."""
        if seconds > 0:
            time.sleep(seconds)


class AsyncDelay:
    """Delay implementation using asyncio.sleep()."""

    async def sleep(self, seconds: float) -> None:
        """Sleep for specified seconds (async)."""
        if seconds > 0:
            await asyncio.sleep(seconds)


class TokenBucketRateLimiter:
    """Token bucket rate limiter implementation.

    Thread-safe implementation for controlling request rates.
    """

    def __init__(
        self,
        rate: float,
        burst: int = 1,
        *,
        clock: TimeDelay | None = None,
    ) -> None:
        """Initialize rate limiter.

        Args:
            rate: Requests per second (e.g., 10 for 10 req/s).
            burst: Maximum burst size (bucket capacity).
            clock: Optional clock for time operations (for testing).
        """
        self._rate = rate
        self._burst = burst
        self._clock = clock or TimeDelay()
        self._tokens = float(burst)
        self._last_update = time.monotonic()
        self._lock = Lock()

    def acquire(self) -> None:
        """Acquire permission to proceed (blocking)."""
        while True:
            with self._lock:
                now = time.monotonic()
                # Add tokens based on elapsed time
                elapsed = now - self._last_update
                self._tokens = min(
                    self._burst, self._tokens + elapsed * self._rate
                )
                self._last_update = now

                if self._tokens >= 1:
                    self._tokens -= 1
                    return

                # Calculate wait time for next token
                wait_time = (1 - self._tokens) / self._rate

            # Sleep outside lock
            self._clock.sleep(wait_time)

    async def async_acquire(self) -> None:
        """Acquire permission to proceed (async)."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_update
                self._tokens = min(
                    self._burst, self._tokens + elapsed * self._rate
                )
                self._last_update = now

                if self._tokens >= 1:
                    self._tokens -= 1
                    return

                wait_time = (1 - self._tokens) / self._rate

            await asyncio.sleep(wait_time)


class RandomDelay:
    """Delay with random jitter.

    Useful for avoiding thundering herd problems.
    """

    def __init__(
        self,
        base_delay: float,
        jitter: float = 0.1,
        *,
        delay: TimeDelay | None = None,
    ) -> None:
        """Initialize random delay.

        Args:
            base_delay: Base delay in seconds.
            jitter: Jitter factor (0.0 to 1.0). Actual delay will be
                base_delay * (1 ± jitter).
            delay: Optional delay implementation (for testing).
        """
        self._base_delay = base_delay
        self._jitter = jitter
        self._delay = delay or TimeDelay()

    def sleep(self) -> None:
        """Sleep with random jitter."""
        jitter_factor = 1.0 + random.uniform(-self._jitter, self._jitter)
        actual_delay = self._base_delay * jitter_factor
        self._delay.sleep(max(0.0, actual_delay))

    async def async_sleep(self) -> None:
        """Sleep with random jitter (async)."""
        jitter_factor = 1.0 + random.uniform(-self._jitter, self._jitter)
        actual_delay = self._base_delay * jitter_factor
        await asyncio.sleep(max(0.0, actual_delay))
