"""Time and cancellation infrastructure implementations."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from typing import Callable


class SystemClock:
    """System clock implementation using datetime and time modules."""

    def now(self) -> datetime:
        """Get current datetime."""
        return datetime.now()

    def sleep(self, seconds: float) -> None:
        """Sleep for specified seconds."""
        time.sleep(seconds)


class ThreadingCancellationToken:
    """Cancellation token using threading.Event.

    Thread-safe implementation for synchronous code.
    """

    def __init__(self, event: threading.Event | None = None) -> None:
        self._event = event or threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation was requested."""
        return self._event.is_set()

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called on cancellation."""
        with self._lock:
            if self._event.is_set():
                # If already cancelled, call immediately
                callback()
            else:
                self._callbacks.append(callback)

    def cancel(self) -> None:
        """Signal cancellation and invoke all registered callbacks."""
        self._event.set()
        with self._lock:
            for callback in self._callbacks:
                try:
                    callback()
                except Exception:
                    # Swallow callback errors
                    pass
            self._callbacks.clear()


class AsyncioCancellationToken:
    """Cancellation token using asyncio.Event.

    For use in async code.
    """

    def __init__(self, event: asyncio.Event | None = None) -> None:
        self._event = event or asyncio.Event()
        self._callbacks: list[Callable[[], None]] = []

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation was requested."""
        return self._event.is_set()

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called on cancellation."""
        if self._event.is_set():
            # If already cancelled, call immediately
            callback()
        else:
            self._callbacks.append(callback)

    def cancel(self) -> None:
        """Signal cancellation and invoke all registered callbacks."""
        self._event.set()
        for callback in self._callbacks:
            try:
                callback()
            except Exception:
                # Swallow callback errors
                pass
        self._callbacks.clear()

    async def wait(self) -> None:
        """Wait until cancellation is signalled."""
        await self._event.wait()
