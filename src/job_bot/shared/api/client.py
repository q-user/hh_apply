"""HH.ru API client for shared kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class HHApiConfig:
    """Configuration for HH API client."""

    base_url: str = "https://api.hh.ru"
    user_agent: str = "job_bot/0.1.0"
    timeout: int = 30


class HHApiClient:
    """Client for interacting with HH.ru API."""

    def __init__(
        self,
        config: HHApiConfig | None = None,
        session: requests.Session | None = None,
        access_token: str | None = None,
    ) -> None:
        self._config = config or HHApiConfig()
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": self._config.user_agent})
        self._access_token = access_token

    def set_access_token(self, token: str) -> None:
        """Set the OAuth access token for authenticated requests."""
        self._access_token = token
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def get(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make a GET request to the API."""
        url = f"{self._config.base_url}{endpoint}"
        response = self._session.get(
            url, params=params, timeout=self._config.timeout
        )
        response.raise_for_status()
        return response.json()

    def post(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a POST request to the API."""
        url = f"{self._config.base_url}{endpoint}"
        response = self._session.post(
            url,
            data=data,
            json=json_data,
            timeout=self._config.timeout,
        )
        response.raise_for_status()
        return response.json()

    def put(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a PUT request to the API."""
        url = f"{self._config.base_url}{endpoint}"
        response = self._session.put(
            url,
            json=json_data,
            timeout=self._config.timeout,
        )
        response.raise_for_status()
        return response.json()

    def delete(self, endpoint: str) -> dict[str, Any]:
        """Make a DELETE request to the API."""
        url = f"{self._config.base_url}{endpoint}"
        response = self._session.delete(url, timeout=self._config.timeout)
        response.raise_for_status()
        return response.json()


def create_hh_api_client(
    access_token: str | None = None,
    session: requests.Session | None = None,
    config: HHApiConfig | None = None,
) -> HHApiClient:
    """Factory function to create an HHApiClient instance."""
    return HHApiClient(
        config=config, session=session, access_token=access_token
    )
