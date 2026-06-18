"""Token-bucket rate limiter for the HH API client (issue #205).

This module implements a pure-stdlib token-bucket rate limiter and
plumbs it into :class:`job_bot.shared.api.client.HHApiClient` so that
every outbound call to ``https://api.hh.ru/...`` is throttled
transparently. HH's per-endpoint rate limits are undocumented and
have caused 429s in bulk apply runs; the rate limiter prevents that
by:

* holding **one** :class:`TokenBucket` per ``(host, path)`` pair —
  requests to different endpoints do not share a budget;
* refilling each bucket at a configured ``refill_rate`` (tokens per
  second) up to ``capacity``;
* blocking the caller (with an optional ``timeout``) when the bucket
  is empty.

The :class:`RateLimitConfig` carries **per-endpoint budgets** as
glob-style ``path`` patterns so admins can tighten or relax specific
endpoints (e.g. ``POST /applications*`` is much more expensive than
``GET /vacancies*``). Anything not matched by an explicit pattern
falls back to ``default_capacity`` / ``default_refill_rate``.

Why a fresh rate limiter (not :class:`job_bot.shared.ports.RateLimiterPort`)?
    The existing :class:`RateLimiterPort` is a *protocol* with a
    single ``acquire()`` method. It is used by VSA slices that want
    their own coarser-grained limiter (a single global
    ``acquire()``). This module ships a concrete
    :class:`RateLimiter` (with the same ``acquire(method, url)``
    shape) that the shared ``HHApiClient`` owns. Slices that want
    finer-grained control can swap it for a custom
    ``RateLimiter`` instance via ``HHApiClient(..., rate_limiter=...)``.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

__all__ = (
    "RateLimitConfig",
    "RateLimiter",
    "RateLimitTimeout",
    "TokenBucket",
)


class RateLimitTimeout(Exception):
    """Raised when :meth:`TokenBucket.acquire` cannot get tokens in time.

    The caller can catch this to decide whether to back off, surface
    a 429 to the user, or fall through to a different code path
    (e.g. drop the request).
    """


# ─── TokenBucket ─────────────────────────────────────────────────


class TokenBucket:
    """Classic token-bucket rate limiter (one bucket per key).

    Tokens refill at ``refill_rate`` per second up to ``capacity``.
    A successful :meth:`try_acquire` subtracts ``n`` tokens and
    returns ``True``; a failed call returns ``False`` *without*
    blocking. :meth:`acquire` wraps :meth:`try_acquire` in a
    blocking loop with an optional ``timeout``.

    Args:
        capacity: maximum number of tokens the bucket can hold
            (also the initial count). Must be ``>= 1``.
        refill_rate: tokens added per second of wall-clock time.
            Must be ``>= 0``; ``0`` means the bucket never
            refills (useful as a "blocked" limiter in tests).
        clock: optional ``() -> float`` callable used as the bucket's
            time source. Defaults to :func:`time.monotonic`. Tests
            inject a fake clock so refill behaviour is deterministic
            without real ``time.sleep``.
    """

    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity!r}")
        if refill_rate < 0:
            raise ValueError(f"refill_rate must be >= 0, got {refill_rate!r}")
        self._capacity = int(capacity)
        # ``0`` is allowed — a bucket that never refills is useful
        # for tests (and for a permanent "blocked" limiter); the
        # acquire loop falls back to a fixed poll interval.
        self._refill_rate = float(refill_rate)
        self._clock = clock
        self._tokens = float(capacity)
        self._last_refill = clock()
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        """Maximum tokens this bucket can hold."""
        return self._capacity

    @property
    def refill_rate(self) -> float:
        """Tokens added per second of wall-clock time."""
        return self._refill_rate

    def _refill_locked(self) -> None:
        """Add tokens for the elapsed time. Caller must hold ``_lock``."""
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._refill_rate,
            )
            self._last_refill = now

    def try_acquire(self, n: int = 1) -> bool:
        """Try to take ``n`` tokens. Non-blocking.

        Returns:
            ``True`` if the tokens were taken, ``False`` if the bucket
            does not currently have enough.
        """
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n!r}")
        with self._lock:
            self._refill_locked()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def acquire(
        self,
        n: int = 1,
        *,
        timeout: float | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Block until ``n`` tokens are available, then take them.

        Args:
            n: tokens to take (must be ``>= 1``).
            timeout: maximum seconds to wait. ``None`` means block
                forever; ``0`` means try once and raise immediately
                if the bucket is empty.
            sleeper: ``(seconds: float) -> None`` callable used to
                wait between retries. Defaults to :func:`time.sleep`.
                Tests inject a no-op or fast-forwarding sleeper.

        Raises:
            RateLimitTimeout: if the timeout elapses before ``n``
                tokens are available.
            ValueError: if ``n < 1``.
        """
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n!r}")
        # Compute the deadline once so wall-clock drift between
        # ``clock()`` and ``sleeper()`` doesn't extend the wait
        # indefinitely.
        deadline = None if timeout is None else self._clock() + timeout
        # Minimum sleep between polls — bounds CPU usage when the
        # refill rate is very high.
        min_sleep = 0.005
        while True:
            if self.try_acquire(n):
                return
            now = self._clock()
            if deadline is not None and now >= deadline:
                raise RateLimitTimeout(
                    f"could not acquire {n} token(s) within {timeout}s"
                )
            # Compute how long to wait until we have ``n`` tokens.
            # We peek at the current token count under the lock so
            # the wait estimate is correct even under contention.
            with self._lock:
                self._refill_locked()
                shortfall = n - self._tokens
                if self._refill_rate > 0 and shortfall > 0:
                    wait = shortfall / self._refill_rate
                else:
                    wait = min_sleep
            if deadline is not None:
                remaining = deadline - self._clock()
                wait = min(wait, remaining)
            sleeper(max(wait, min_sleep))


# ─── RateLimitConfig ─────────────────────────────────────────────


@dataclass(frozen=True)
class RateLimitConfig:
    """Per-endpoint rate budgets for :class:`RateLimiter`.

    Attributes:
        default_capacity: bucket capacity for any ``(host, path)``
            not matched by an entry in :attr:`endpoints`. Defaults
            to ``10``.
        default_refill_rate: bucket refill rate (tokens/sec) for
            any unmatched ``(host, path)``. Defaults to ``10.0``
            (≈ 10 req/s, well under the documented HH quota).
        endpoints: ``(host, path_pattern)`` → ``(capacity, refill_rate)``.
            ``path_pattern`` is a glob (e.g. ``/vacancies*``)
            matched via :func:`fnmatch.fnmatchcase` against the
            request's path. The first matching pattern wins; order
            in the dict controls precedence on overlap. If no
            pattern matches for a host, the default is used.
    """

    default_capacity: int = 10
    default_refill_rate: float = 10.0
    endpoints: dict[tuple[str, str], tuple[int, float]] = field(
        default_factory=dict
    )

    @classmethod
    def with_hh_defaults(cls) -> RateLimitConfig:
        """Sensible per-endpoint budgets for the HH.ru API.

        Patterns follow HH's rough public guidance plus a few
        empirically observed limits from production traffic
        (issue #205).  Defaults:

        * ``GET /vacancies*`` — 10 req/s (search is read-heavy and
          cheap).
        * ``POST /negotiations*`` — 1 req / 3 s (apply is
          expensive; we throttle hard).
        * ``GET /me*`` / ``GET /resumes*`` — 5 req/s (auth/profile
          endpoints; common in the apply loop).
        * everything else — 10 req/s.
        """
        return cls(
            default_capacity=10,
            default_refill_rate=10.0,
            endpoints={
                ("api.hh.ru", "/vacancies*"): (10, 10.0),
                ("api.hh.ru", "/negotiations*"): (1, 1.0 / 3.0),
                ("api.hh.ru", "/me*"): (5, 5.0),
                ("api.hh.ru", "/resumes*"): (5, 5.0),
            },
        )


# ─── RateLimiter ─────────────────────────────────────────────────


class RateLimiter:
    """Per-``(host, path)`` rate limiter (one :class:`TokenBucket` per key).

    The HTTP call site invokes :meth:`acquire` before the request
    goes out. If the bucket for that key is empty, the call blocks
    (up to ``timeout``) until tokens refill, or raises
    :class:`RateLimitTimeout`.

    Buckets are created lazily on first use and held for the
    lifetime of the limiter. Thread-safety:

    * the bucket map is protected by ``_lock`` (insertion / lookup);
    * each :class:`TokenBucket` has its own internal lock (token
      manipulation).

    Args:
        config: per-endpoint budgets. ``None`` uses
            :class:`RateLimitConfig` defaults.
        clock: time source forwarded to every bucket. Tests inject
            a fake clock to keep refill behaviour deterministic.
        sleeper: blocking-wait primitive forwarded to every
            bucket's :meth:`TokenBucket.acquire`. Tests inject a
            no-op or fast-forwarding sleeper.
    """

    def __init__(
        self,
        config: RateLimitConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config or RateLimitConfig()
        self._clock = clock
        self._sleeper = sleeper
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._lock = threading.Lock()

    @property
    def config(self) -> RateLimitConfig:
        """The active rate-limit configuration."""
        return self._config

    def _lookup_budget(self, host: str, path: str) -> tuple[int, float]:
        """Return ``(capacity, refill_rate)`` for ``(host, path)``.

        Patterns in :attr:`RateLimitConfig.endpoints` are scanned
        in insertion order; the first glob match wins. Unknown
        ``(host, path)`` falls back to the default.
        """
        for (cfg_host, pattern), budget in self._config.endpoints.items():
            if cfg_host != host:
                continue
            if fnmatch.fnmatchcase(path, pattern):
                return budget
        return (
            self._config.default_capacity,
            self._config.default_refill_rate,
        )

    def _bucket_for(self, host: str, path: str) -> TokenBucket:
        """Return the :class:`TokenBucket` for ``(host, path)``,
        creating it on first use. Thread-safe.
        """
        with self._lock:
            bucket = self._buckets.get((host, path))
            if bucket is not None:
                return bucket
            capacity, refill_rate = self._lookup_budget(host, path)
            bucket = TokenBucket(
                capacity=capacity,
                refill_rate=refill_rate,
                clock=self._clock,
            )
            self._buckets[(host, path)] = bucket
            return bucket

    def acquire(
        self,
        method: str,
        url: str,
        *,
        timeout: float | None = None,
    ) -> None:
        """Block until one token is available for ``(host, path)``.

        The HTTP method is included in the signature for symmetry
        with future per-method budgets; the current implementation
        keys buckets on ``(host, path)`` only.

        Args:
            method: HTTP verb (e.g. ``"GET"``). Currently unused for
                bucket selection; reserved for future per-method
                budgets.
            url: full request URL. Only ``netloc`` (host) and
                ``path`` are used; query string and fragment are
                ignored.
            timeout: forwarded to :meth:`TokenBucket.acquire`.
                ``None`` blocks indefinitely, ``0`` tries once and
                raises on failure.

        Raises:
            RateLimitTimeout: if the timeout elapses before a token
                becomes available.
        """
        # Normalise the URL defensively so callers can pass either
        # a full URL (``https://api.hh.ru/foo``) or a path
        # (``/foo``) with the same result. ``urlparse`` on a bare
        # path leaves ``netloc`` empty — we treat that as the
        # default host.
        parsed = urlparse(url)
        if parsed.netloc:
            host = parsed.netloc
        else:
            host = ""
        path = parsed.path or "/"
        bucket = self._bucket_for(host, path)
        bucket.acquire(n=1, timeout=timeout, sleeper=self._sleeper)


# ─── NullRateLimiter ─────────────────────────────────────────────


class NullRateLimiter:
    """No-op stand-in for :class:`RateLimiter`.

    Accepts the same ``acquire(method, url)`` call shape so it can
    be passed where a :class:`RateLimiter` is expected. Useful for
    tests that want to bypass rate limiting without rewriting
    call sites, or for opt-out wiring in production.
    """

    def acquire(
        self,
        method: str,  # noqa: ARG002  — API parity
        url: str,  # noqa: ARG002 — API parity
        *,
        timeout: float | None = None,  # noqa: ARG002
    ) -> None:
        """No-op. Always returns immediately."""
        return None

    def __getattr__(self, name: str) -> Any:
        # Defensive: surface a clear error if someone accesses a
        # real attribute we don't have (e.g. ``_buckets``).
        raise AttributeError(f"NullRateLimiter has no attribute {name!r}")
