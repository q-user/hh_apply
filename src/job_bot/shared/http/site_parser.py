"""HTTP infrastructure implementations."""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__package__)


class RequestsSiteParser:
    """Site parser implementation using requests.

    Uses only public requests API, no private attributes.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        timeout: float = 10.0,
        user_agent: str | None = None,
    ) -> None:
        """Initialize site parser.

        Args:
            session: Optional requests session to use. Creates new one if not provided.
            timeout: Request timeout in seconds.
            user_agent: Optional User-Agent header.
        """
        self._session = session or requests.Session()
        self._timeout = timeout
        self._user_agent = user_agent

    def parse_site(self, url: str) -> dict[str, Any]:
        """Parse a site URL and extract metadata.

        Args:
            url: Site URL to parse.

        Returns:
            Dictionary with keys: title, description, generator, emails,
            server_name, powered_by, ip_address.
        """
        headers = {}
        if self._user_agent:
            headers["User-Agent"] = self._user_agent

        try:
            with self._session.get(
                url, timeout=self._timeout, headers=headers
            ) as r:
                r.raise_for_status()
                return self._parse_response(r)
        except requests.RequestException as ex:
            logger.error("Failed to parse site %s: %s", url, ex)
            return {
                "title": "",
                "description": "",
                "generator": "",
                "emails": [],
                "server_name": None,
                "powered_by": None,
                "ip_address": None,
            }

    def _parse_response(self, response: requests.Response) -> dict[str, Any]:
        """Parse HTTP response and extract metadata."""

        def val(m: re.Match[str] | None) -> str:
            return unescape(m.group(1)) if m else ""

        title = val(
            re.search(r"<title>(.*?)</title>", response.text, re.I | re.S)
        )
        description = val(
            re.search(
                r'<meta name="description" content="(.*?)"',
                response.text,
                re.I,
            )
        )
        generator = val(
            re.search(
                r'<meta name="generator" content="(.*?)"',
                response.text,
                re.I,
            )
        )

        emails = set(
            m.group(0)
            # Exclude garbage like energy-software-slider-225x225@2x.png
            for m in re.finditer(
                r"\b[a-z][a-z0-9_.-]+@("
                r"[a-z0-9][a-z0-9-]+)(?!\.(?:png|jpe?g|bmp|gif|ico|"
                r"js|css)\b)(\.[a-z0-9][a-z0-9-]+)+\b",
                response.text,
            )
        )

        # Try to get IP address from connection
        ip_address = None
        try:
            if response.raw._connection and response.raw._connection.sock:
                ip_address = response.raw._connection.sock.getpeername()[0]
        except Exception:  # noqa: BLE001  # touches urllib3 private API; any failure means IP can't be extracted
            pass

        return {
            "title": title,
            "description": description,
            "generator": generator,
            "emails": list(emails),
            "server_name": response.headers.get("Server"),
            "powered_by": response.headers.get("X-Powered-By"),
            "ip_address": ip_address,
        }


class RequestsHttpClient:
    """HTTP client implementation using requests with retry logic.

    Provides session management, retries, and timeouts.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
    ) -> None:
        """Initialize HTTP client.

        Args:
            session: Optional requests session to use. Creates new one with retry logic if not provided.
            timeout: Default request timeout in seconds.
            max_retries: Maximum number of retries.
            backoff_factor: Backoff factor for retries.
            status_forcelist: HTTP status codes to retry.
        """
        self._timeout = timeout

        if session is not None:
            self._session = session
        else:
            self._session = requests.Session()
            self._configure_retries(
                max_retries, backoff_factor, status_forcelist
            )

    def _configure_retries(
        self,
        max_retries: int,
        backoff_factor: float,
        status_forcelist: tuple[int, ...],
    ) -> None:
        """Configure retry strategy for the session."""
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """Perform GET request.

        Args:
            url: Request URL.
            **kwargs: Additional arguments passed to requests.

        Returns:
            Response object.
        """
        kwargs.setdefault("timeout", self._timeout)
        return self._session.get(url, **kwargs)

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
        kwargs.setdefault("timeout", self._timeout)
        return self._session.post(url, data=data, **kwargs)

    @property
    def session(self) -> requests.Session:
        """Get underlying session for advanced usage."""
        return self._session
