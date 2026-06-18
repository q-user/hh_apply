"""Tests for the token-bucket rate limiter (issue #205).

Covers:

* :class:`TokenBucket` — acquire consumes tokens, refills over time,
  blocks until available, raises on timeout.
* :class:`RateLimiter` — per-(host, path) isolation, default config for
  unknown endpoints, thread-safety of the bucket map.
* Integration — :class:`HHApiClient` consults the rate limiter before
  every request.

All time-dependent tests use **clock injection** (a fake
``Callable[[], float]`` passed to the bucket) instead of real
``time.sleep`` / ``time.monotonic``. This keeps the suite deterministic
and fast (no flaky CI from clock skew).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from job_bot.shared.api.client import HHApiClient, HHApiConfig
from job_bot.shared.api.rate_limit import (
    RateLimitConfig,
    RateLimiter,
    RateLimitTimeout,
    TokenBucket,
)

# ─── Test helpers ────────────────────────────────────────────────


class FakeClock:
    """A fake monotonic clock that can be advanced manually.

    Returned by :func:`make_clock` and used in place of
    :func:`time.monotonic` so token-bucket tests don't depend on real
    time. ``advance(dt)`` moves the clock forward, ``sleep`` is a no-op
    so the suite doesn't actually wait.
    """

    def __init__(self) -> None:
        self._now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self._now

    def advance(self, dt: float) -> None:
        self._now += dt

    def sleep(self, dt: float) -> None:
        # Record sleeps so tests can assert on polling behaviour, but
        # do NOT actually sleep — keeps the suite fast.
        self.sleeps.append(dt)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


# ─── TokenBucket ─────────────────────────────────────────────────


def test_token_bucket_starts_full(fake_clock: FakeClock) -> None:
    """A fresh bucket has ``capacity`` tokens ready to spend."""
    bucket = TokenBucket(capacity=5, refill_rate=1.0, clock=fake_clock)
    assert bucket.try_acquire() is True
    # 4 tokens left after one acquire.
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    # 1 left → can still acquire 1.
    assert bucket.try_acquire() is True
    # 0 left → next acquire fails.
    assert bucket.try_acquire() is False


def test_token_bucket_acquire_consumes_tokens(
    fake_clock: FakeClock,
) -> None:
    """N successful ``try_acquire`` calls consume N tokens."""
    bucket = TokenBucket(capacity=3, refill_rate=1.0, clock=fake_clock)
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    # Fourth acquire fails — bucket is empty.
    assert bucket.try_acquire() is False


def test_token_bucket_acquire_n_consumes_n_tokens(
    fake_clock: FakeClock,
) -> None:
    """Multi-token acquire subtracts the full count."""
    bucket = TokenBucket(capacity=10, refill_rate=1.0, clock=fake_clock)
    assert bucket.try_acquire(4) is True
    assert bucket.try_acquire(6) is True
    # Bucket empty.
    assert bucket.try_acquire(1) is False
    assert bucket.try_acquire(2) is False


def test_token_bucket_refills_over_time(fake_clock: FakeClock) -> None:
    """Tokens accumulate as time passes at ``refill_rate`` per second.

    We exhaust the bucket, advance the clock by ``dt`` seconds, and
    expect the bucket to have ``dt * refill_rate`` new tokens (capped
    at capacity).
    """
    bucket = TokenBucket(capacity=5, refill_rate=2.0, clock=fake_clock)
    # Drain the bucket.
    for _ in range(5):
        assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False

    # Advance 1.5s → 1.5 * 2.0 = 3 tokens should refill.
    fake_clock.advance(1.5)
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    # Only 3 refilled, not 4.
    assert bucket.try_acquire() is False


def test_token_bucket_refill_caps_at_capacity(
    fake_clock: FakeClock,
) -> None:
    """Idle bucket never exceeds ``capacity`` tokens."""
    bucket = TokenBucket(capacity=3, refill_rate=10.0, clock=fake_clock)
    fake_clock.advance(100.0)  # 1000 tokens worth of refill time
    # Should still be capped at capacity = 3.
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False


def test_token_bucket_acquire_blocks_until_available(
    fake_clock: FakeClock,
) -> None:
    """``acquire()`` with ``timeout=None`` blocks until tokens are
    available. We wire the fake clock to fast-forward while
    ``acquire`` is sleeping so the test is deterministic.
    """
    bucket = TokenBucket(capacity=1, refill_rate=2.0, clock=fake_clock)
    assert bucket.try_acquire() is True
    # Bucket is now empty.

    # Schedule the clock to advance when ``sleeper`` is called —
    # simulates 0.5s of real time passing.
    def fake_sleeper(dt: float) -> None:
        fake_clock.advance(dt)

    # The acquire should succeed after 0.5s of wall-clock equivalent
    # (0.5 * 2.0 = 1 token refilled).
    bucket.acquire(n=1, timeout=None, sleeper=fake_sleeper)


def test_token_bucket_acquire_raises_on_timeout(
    fake_clock: FakeClock,
) -> None:
    """``acquire(timeout=0)`` on an empty bucket raises immediately.

    The test uses a no-op sleeper so we don't accidentally fast-forward
    during the call.
    """
    bucket = TokenBucket(capacity=1, refill_rate=0.1, clock=fake_clock)
    assert bucket.try_acquire() is True

    with pytest.raises(RateLimitTimeout):
        bucket.acquire(n=1, timeout=0.0, sleeper=lambda _dt: None)


def test_token_bucket_acquire_timeout_too_short(
    fake_clock: FakeClock,
) -> None:
    """A positive but too-small timeout raises ``RateLimitTimeout``.

    The sleeper is wired to fast-forward the clock — this models
    real-world ``time.sleep`` and keeps the test deterministic.
    """
    bucket = TokenBucket(capacity=1, refill_rate=1.0, clock=fake_clock)
    assert bucket.try_acquire() is True

    # Sleeper advances the clock by the requested amount, so the
    # timeout check (``now >= deadline``) eventually fires.
    def advancing_sleeper(dt: float) -> None:
        fake_clock.advance(dt)

    # Refill rate is 1.0/s; after 0.1s we'd have 0.1 tokens, still
    # < 1, so a 0.1s timeout can't possibly succeed.
    with pytest.raises(RateLimitTimeout):
        bucket.acquire(n=1, timeout=0.1, sleeper=advancing_sleeper)


# ─── RateLimitConfig / RateLimiter ──────────────────────────────


def test_rate_limit_config_defaults_are_sensible() -> None:
    """Default config: 10 tokens capacity, 10 tokens/sec refill."""
    config = RateLimitConfig()
    assert config.default_capacity == 10
    assert config.default_refill_rate == 10.0
    assert config.endpoints == {}


def test_rate_limiter_default_config_used_for_unknown(
    fake_clock: FakeClock,
) -> None:
    """An unknown (host, path) gets the default bucket."""
    config = RateLimitConfig(default_capacity=7, default_refill_rate=3.5)
    limiter = RateLimiter(
        config=config, clock=fake_clock, sleeper=lambda _dt: None
    )
    # Drain the default bucket.
    for _ in range(7):
        limiter.acquire("GET", "https://api.hh.ru/unknown")
    # No more tokens at default rate → next call blocks / fails.
    with pytest.raises(RateLimitTimeout):
        limiter.acquire(
            "GET",
            "https://api.hh.ru/unknown",
            timeout=0.0,
        )


def test_rate_limiter_per_host_path_isolation(
    fake_clock: FakeClock,
) -> None:
    """Different (host, path) keys get different buckets."""
    config = RateLimitConfig(default_capacity=2, default_refill_rate=1.0)
    limiter = RateLimiter(
        config=config, clock=fake_clock, sleeper=lambda _dt: None
    )
    # Drain the /foo bucket.
    limiter.acquire("GET", "https://api.hh.ru/foo")
    limiter.acquire("GET", "https://api.hh.ru/foo")
    with pytest.raises(RateLimitTimeout):
        limiter.acquire("GET", "https://api.hh.ru/foo", timeout=0.0)

    # /bar is independent — should still have its full capacity.
    limiter.acquire("GET", "https://api.hh.ru/bar")
    limiter.acquire("GET", "https://api.hh.ru/bar")
    with pytest.raises(RateLimitTimeout):
        limiter.acquire("GET", "https://api.hh.ru/bar", timeout=0.0)

    # And the /foo bucket is still empty — confirms isolation, not
    # cross-contamination.
    with pytest.raises(RateLimitTimeout):
        limiter.acquire("GET", "https://api.hh.ru/foo", timeout=0.0)


def test_rate_limiter_different_hosts_isolated(
    fake_clock: FakeClock,
) -> None:
    """Two requests to the same path on different hosts are isolated."""
    config = RateLimitConfig(default_capacity=1, default_refill_rate=0.1)
    limiter = RateLimiter(
        config=config, clock=fake_clock, sleeper=lambda _dt: None
    )
    limiter.acquire("GET", "https://api.hh.ru/x")
    with pytest.raises(RateLimitTimeout):
        limiter.acquire("GET", "https://api.hh.ru/x", timeout=0.0)
    # different host → fresh bucket
    limiter.acquire("GET", "https://api.example.com/x")


def test_rate_limiter_path_pattern_matches_with_glob(
    fake_clock: FakeClock,
) -> None:
    """A pattern like ``/vacancies*`` matches any path under it."""
    config = RateLimitConfig(
        default_capacity=10,
        default_refill_rate=10.0,
        endpoints={
            ("api.hh.ru", "/vacancies*"): (3, 1.0),
        },
    )
    limiter = RateLimiter(
        config=config, clock=fake_clock, sleeper=lambda _dt: None
    )
    # /vacancies matches the pattern → custom 3-cap bucket.
    limiter.acquire("GET", "https://api.hh.ru/vacancies")
    limiter.acquire("GET", "https://api.hh.ru/vacancies")
    limiter.acquire("GET", "https://api.hh.ru/vacancies")
    with pytest.raises(RateLimitTimeout):
        limiter.acquire("GET", "https://api.hh.ru/vacancies", timeout=0.0)

    # /vacancies/42 also matches the pattern → fresh bucket.
    limiter.acquire("GET", "https://api.hh.ru/vacancies/42")
    limiter.acquire("GET", "https://api.hh.ru/vacancies/42")
    limiter.acquire("GET", "https://api.hh.ru/vacancies/42")
    with pytest.raises(RateLimitTimeout):
        limiter.acquire("GET", "https://api.hh.ru/vacancies/42", timeout=0.0)

    # /employers is not in the pattern → default bucket (10-cap).
    for _ in range(10):
        limiter.acquire("GET", "https://api.hh.ru/employers")
    with pytest.raises(RateLimitTimeout):
        limiter.acquire("GET", "https://api.hh.ru/employers", timeout=0.0)


def test_rate_limiter_first_acquire_creates_bucket(
    fake_clock: FakeClock,
) -> None:
    """A bucket is lazily created on the first acquire for a key."""
    limiter = RateLimiter(
        config=RateLimitConfig(),
        clock=fake_clock,
        sleeper=lambda _dt: None,
    )
    # Internal state should be empty before any acquire.
    assert limiter._buckets == {}  # noqa: SLF001
    limiter.acquire("GET", "https://api.hh.ru/vacancies")
    assert ("api.hh.ru", "/vacancies") in limiter._buckets  # noqa: SLF001


def test_rate_limiter_path_extracted_from_url(
    fake_clock: FakeClock,
) -> None:
    """The bucket key is the URL's ``(netloc, path)`` — query/fragment
    are ignored.
    """
    limiter = RateLimiter(
        config=RateLimitConfig(default_capacity=1, default_refill_rate=0.1),
        clock=fake_clock,
        sleeper=lambda _dt: None,
    )
    # Same path, different query strings → same bucket.
    limiter.acquire("GET", "https://api.hh.ru/vacancies?page=0&per_page=10")
    with pytest.raises(RateLimitTimeout):
        limiter.acquire(
            "GET",
            "https://api.hh.ru/vacancies?page=1",
            timeout=0.0,
        )


# ─── HHApiClient integration ────────────────────────────────────


def _build_session() -> tuple[MagicMock, MagicMock]:
    """Create a mocked ``requests.Session`` with a 200 OK response.

    Returns ``(session, response)`` — ``session.get/post/...`` return
    ``response``. The response has ``status_code = 200`` and a JSON
    body, so :class:`HHApiClient` can parse it via ``response.json()``.
    """
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True}

    session = MagicMock()
    session.get.return_value = response
    session.post.return_value = response
    session.put.return_value = response
    session.delete.return_value = response
    session.head.return_value = response
    return session, response


def test_hh_api_client_consults_rate_limiter_on_get() -> None:
    """``HHApiClient.get()`` calls ``RateLimiter.acquire`` first."""
    session, _response = _build_session()
    mock_limiter = MagicMock(spec=RateLimiter)
    client = HHApiClient(
        config=HHApiConfig(), session=session, rate_limiter=mock_limiter
    )

    client.get("/vacancies")

    # acquire was called exactly once, before the request went out.
    assert mock_limiter.acquire.call_count == 1
    call_args = mock_limiter.acquire.call_args
    assert call_args.args[0] == "GET"
    assert "api.hh.ru" in call_args.args[1]
    assert "/vacancies" in call_args.args[1]
    # The session.get was called after acquire.
    assert session.get.called


def test_hh_api_client_consults_rate_limiter_on_post() -> None:
    """POST requests go through the rate limiter too."""
    session, _response = _build_session()
    mock_limiter = MagicMock(spec=RateLimiter)
    client = HHApiClient(
        config=HHApiConfig(), session=session, rate_limiter=mock_limiter
    )

    client.post("/negotiations", json_data={"vacancy_id": "42"})

    assert mock_limiter.acquire.call_count == 1
    assert mock_limiter.acquire.call_args.args[0] == "POST"


def test_hh_api_client_consults_rate_limiter_on_put_and_delete() -> None:
    """PUT and DELETE also go through the rate limiter."""
    session, _response = _build_session()
    mock_limiter = MagicMock(spec=RateLimiter)
    client = HHApiClient(
        config=HHApiConfig(), session=session, rate_limiter=mock_limiter
    )

    client.put("/x", json_data={"a": 1})
    client.delete("/x")

    methods = [call.args[0] for call in mock_limiter.acquire.call_args_list]
    assert methods == ["PUT", "DELETE"]


def test_hh_api_client_consults_rate_limiter_on_ping() -> None:
    """``ping()`` (HEAD request) also consults the rate limiter."""
    session, _response = _build_session()
    mock_limiter = MagicMock(spec=RateLimiter)
    client = HHApiClient(
        config=HHApiConfig(), session=session, rate_limiter=mock_limiter
    )

    client.ping()

    assert mock_limiter.acquire.call_count == 1
    assert mock_limiter.acquire.call_args.args[0] == "HEAD"


def test_hh_api_client_uses_default_rate_limiter() -> None:
    """When no ``rate_limiter`` is passed, a default one is used."""
    session, _response = _build_session()
    client = HHApiClient(config=HHApiConfig(), session=session)
    # Default rate_limiter is created and used.
    assert isinstance(client._rate_limiter, RateLimiter)  # noqa: SLF001
    # And the request goes through without raising.
    result = client.get("/vacancies")
    assert result == {"ok": True}


def test_hh_api_client_acquire_failure_propagates() -> None:
    """If the rate limiter raises, the call is not made."""
    session, _response = _build_session()
    mock_limiter = MagicMock(spec=RateLimiter)
    mock_limiter.acquire.side_effect = RateLimitTimeout("rate limited")

    client = HHApiClient(
        config=HHApiConfig(), session=session, rate_limiter=mock_limiter
    )

    with pytest.raises(RateLimitTimeout):
        client.get("/vacancies")
    # And the actual session.get was NEVER called.
    assert not session.get.called


# ─── RateLimiter thread-safety smoke test ────────────────────────


def test_rate_limiter_thread_safety_smoke() -> None:
    """Many parallel acquires against the same key never exceed the
    bucket capacity. This is a smoke test — the lock inside
    :class:`TokenBucket` is the real guarantee, this just confirms
    the rate limiter's outer lock works too.
    """
    import threading

    config = RateLimitConfig(
        default_capacity=10,
        default_refill_rate=0.0,  # no refill
    )
    limiter = RateLimiter(
        config=config, clock=lambda: 0.0, sleeper=lambda _dt: None
    )
    successes = 0
    failures = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal successes, failures
        try:
            limiter.acquire("GET", "https://api.hh.ru/x", timeout=0.0)
            with lock:
                successes += 1
        except RateLimitTimeout:
            with lock:
                failures += 1

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Capacity is 10, no refill → exactly 10 successes and 40 failures.
    assert successes == 10
    assert failures == 40
