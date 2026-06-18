"""HH.ru API client for shared kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import requests

from job_bot.shared.api.rate_limit import (
    RateLimitConfig,
    RateLimiter,
)


@dataclass
class HHApiConfig:
    """Configuration for HH API client."""

    base_url: str = "https://api.hh.ru"
    user_agent: str = "job_bot/0.1.0"
    timeout: int = 30


class HHApiClient:
    """Client for interacting with HH.ru API.

    Every outbound request is gated by :class:`RateLimiter` so
    per-endpoint throttling is applied transparently (issue #205).
    The default rate limiter is a fresh :class:`RateLimiter` with
    :class:`RateLimitConfig` defaults (10 req/s). Tests / callers
    can inject a custom limiter via ``rate_limiter=`` — typically a
    :class:`~unittest.mock.MagicMock` or a hand-rolled fake.
    """

    def __init__(
        self,
        config: HHApiConfig | None = None,
        session: requests.Session | None = None,
        access_token: str | None = None,
        rate_limiter: RateLimiter | None = None,
        rate_limit_config: RateLimitConfig | None = None,
    ) -> None:
        self._config = config or HHApiConfig()
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": self._config.user_agent})
        self._access_token = access_token
        # Default limiter is fresh per-instance. Pass a custom
        # ``rate_limiter`` to share buckets across multiple clients
        # (e.g. when several slices share one HTTP session).
        if rate_limiter is not None:
            self._rate_limiter = rate_limiter
        else:
            self._rate_limiter = RateLimiter(config=rate_limit_config)

    def set_access_token(self, token: str) -> None:
        """Set the OAuth access token for authenticated requests."""
        self._access_token = token
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def get(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make a GET request to the API."""
        url = f"{self._config.base_url}{endpoint}"
        self._rate_limiter.acquire("GET", url)
        response = self._session.get(
            url, params=params, timeout=self._config.timeout
        )
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())

    def post(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a POST request to the API."""
        url = f"{self._config.base_url}{endpoint}"
        self._rate_limiter.acquire("POST", url)
        response = self._session.post(
            url,
            data=data,
            json=json_data,
            timeout=self._config.timeout,
        )
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())

    def put(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a PUT request to the API."""
        url = f"{self._config.base_url}{endpoint}"
        self._rate_limiter.acquire("PUT", url)
        response = self._session.put(
            url,
            json=json_data,
            timeout=self._config.timeout,
        )
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())

    def delete(self, endpoint: str) -> dict[str, Any]:
        """Make a DELETE request to the API."""
        url = f"{self._config.base_url}{endpoint}"
        self._rate_limiter.acquire("DELETE", url)
        response = self._session.delete(url, timeout=self._config.timeout)
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())

    def ping(self) -> None:
        """Lightweight liveness probe against the HH API root.

        Sends a ``HEAD`` to ``https://api.hh.ru/`` and raises on any
        non-2xx response or network error. Designed for the
        :class:`DefaultHealthChecks` readiness probe -- the network
        round-trip and 4xx-mapping is the only thing we care about
        (we deliberately do *not* parse the response body).

        Raises:
            requests.HTTPError: if the response status is >= 400.
            requests.RequestException: on connection / timeout
                failures (propagated from the underlying session).
        """
        self._rate_limiter.acquire("HEAD", self._config.base_url)
        response = self._session.head(
            self._config.base_url, timeout=self._config.timeout
        )
        response.raise_for_status()


def create_hh_api_client(
    access_token: str | None = None,
    session: requests.Session | None = None,
    config: HHApiConfig | None = None,
    rate_limiter: RateLimiter | None = None,
    rate_limit_config: RateLimitConfig | None = None,
) -> HHApiClient:
    """Factory function to create an HHApiClient instance."""
    return HHApiClient(
        config=config,
        session=session,
        access_token=access_token,
        rate_limiter=rate_limiter,
        rate_limit_config=rate_limit_config,
    )
