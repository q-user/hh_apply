"""Vacancy description fetcher infrastructure implementations."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__package__)


class CachedVacancyDescriptionFetcher:
    """Vacancy description fetcher with in-memory TTL cache."""

    def __init__(
        self,
        session: requests.Session,
        *,
        base_url: str = "https://hh.ru/vacancy/",
        ttl: float = 300.0,
        timeout: float = 10.0,
    ) -> None:
        """Initialize vacancy fetcher.

        Args:
            session: Requests session to use for HTTP requests.
            base_url: Base URL for vacancy pages.
            ttl: Cache time-to-live in seconds.
            timeout: Request timeout in seconds.
        """
        self._session = session
        self._base_url = base_url
        self._ttl = ttl
        self._timeout = timeout
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}

    def fetch(self, vacancy_id: str) -> dict[str, Any] | None:
        """Fetch full vacancy description by ID.

        Args:
            vacancy_id: Vacancy identifier.

        Returns:
            Vacancy data dict or None if not found.
        """
        # Check cache
        now = time.monotonic()
        if vacancy_id in self._cache:
            data, cached_at = self._cache[vacancy_id]
            if now - cached_at < self._ttl:
                logger.debug("Cache hit for vacancy %s", vacancy_id)
                return data
            else:
                logger.debug("Cache expired for vacancy %s", vacancy_id)
                del self._cache[vacancy_id]

        # Fetch from network
        url = f"{self._base_url}{vacancy_id}"
        try:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()

            # Parse JSON from page (HH embeds vacancy data in JSON)
            vacancy_data = self._parse_vacancy_page(response.text)
            if vacancy_data:
                self._cache[vacancy_id] = (vacancy_data, now)
                logger.debug("Cached vacancy %s", vacancy_id)
                return vacancy_data

            logger.warning("Failed to parse vacancy data for %s", vacancy_id)
            return None

        except requests.RequestException as ex:
            logger.error("Failed to fetch vacancy %s: %s", vacancy_id, ex)
            return None

    def _parse_vacancy_page(self, html: str) -> dict[str, Any] | None:
        """Parse vacancy data from HTML page.

        HH.ru embeds vacancy data in a JSON blob in the page.
        """
        import re

        from job_bot.shared.utils.json_utils import JSONDecoder

        decoder = JSONDecoder()

        # Look for the vacancy data in the page
        # HH typically embeds it in a script tag or as window.initialState
        patterns = [
            r"window\.initialState\s*=\s*(\{.*?\});",
            r'"vacancy":\s*(\{.*?\})',
            r'"vacancyData":\s*(\{.*?\})',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    return decoder.raw_decode(match.group(1))[0]
                except ValueError:
                    # json.JSONDecodeError is a subclass of ValueError; any
                    # other ValueError from the decoder means the match
                    # wasn't a real JSON object, so try the next pattern.
                    continue

        return None

    def clear_cache(self) -> None:
        """Clear the cache."""
        self._cache.clear()
        logger.debug("Vacancy cache cleared")

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        now = time.monotonic()
        valid = sum(
            1
            for _, cached_at in self._cache.values()
            if now - cached_at < self._ttl
        )
        expired = len(self._cache) - valid
        return {
            "total": len(self._cache),
            "valid": valid,
            "expired": expired,
            "ttl_seconds": self._ttl,
        }
