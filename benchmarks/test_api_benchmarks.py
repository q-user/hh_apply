"""HH API Client Benchmarks.

Benchmarks for measuring API client performance including:
- Request latency (p50, p95, p99)
- Rate limiting behavior
- Retry logic performance
- Connection pooling
- Pagination performance
"""

import json
import time
from unittest.mock import Mock

import pytest
import requests

from hh_applicant_tool.api.client import ApiClient, BaseClient, OAuthClient


class MockResponse:
    """Mock response for testing without network calls."""

    def __init__(
        self, status_code: int = 200, json_data: dict = None, text: str = ""
    ):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text or json.dumps(self._json_data)
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error")


@pytest.fixture
def mock_session():
    """Create a mock requests session."""
    session = Mock(spec=requests.Session)
    return session


@pytest.fixture
def base_client(mock_session):
    """Create a BaseClient with mock session."""
    client = BaseClient(
        base_url="https://api.test/",
        session=mock_session,
        delay=0.0,  # No delay for benchmarks
        timeout=30.0,
    )
    return client


@pytest.fixture
def api_client(mock_session):
    """Create an ApiClient with mock session."""
    client = ApiClient(
        base_url="https://api.test/",
        session=mock_session,
        delay=0.0,
        timeout=30.0,
        access_token="USER_test_token",
    )
    return client


@pytest.fixture
def oauth_client(mock_session):
    """Create an OAuthClient with mock session."""
    client = OAuthClient(
        base_url="https://oauth.test/",
        session=mock_session,
        delay=0.0,
        timeout=30.0,
        client_id="test_client",
        client_secret="test_secret",
    )
    return client


# ============================================================================
# BaseClient Benchmarks
# ============================================================================


class TestBaseClientBenchmarks:
    """Benchmarks for BaseClient core operations."""

    def test_get_request_latency(self, benchmark, base_client, mock_session):
        """Benchmark GET request latency."""
        mock_session.request.return_value = MockResponse(200, {"data": "test"})

        def run():
            return base_client.get("/test")

        result = benchmark(run)
        assert result == {"data": "test"}

    def test_post_request_latency(self, benchmark, base_client, mock_session):
        """Benchmark POST request latency."""
        mock_session.request.return_value = MockResponse(200, {"created": True})

        def run():
            return base_client.post("/test", {"key": "value"})

        result = benchmark(run)
        assert result == {"created": True}

    def test_request_with_delay(self, benchmark, base_client, mock_session):
        """Benchmark request with rate limiting delay."""
        mock_session.request.return_value = MockResponse(200, {"data": "test"})

        def run():
            return base_client.get("/test", delay=0.1)

        result = benchmark(run)
        assert result == {"data": "test"}

    def test_concurrent_requests_simulation(
        self, benchmark, base_client, mock_session
    ):
        """Benchmark simulated concurrent request handling."""
        mock_session.request.return_value = MockResponse(200, {"data": "test"})

        def run():
            # Simulate 10 rapid requests
            results = []
            for _ in range(10):
                results.append(base_client.get("/test"))
            return results

        result = benchmark(run)
        assert len(result) == 10

    def test_error_handling_latency(self, benchmark, base_client, mock_session):
        """Benchmark error handling performance."""
        mock_session.request.return_value = MockResponse(404, {}, "Not Found")

        def run():
            try:
                base_client.get("/nonexistent")
            except Exception:
                pass

        benchmark(run)


# ============================================================================
# ApiClient Benchmarks
# ============================================================================


class TestApiClientBenchmarks:
    """Benchmarks for ApiClient with authentication."""

    def test_authenticated_get_latency(
        self, benchmark, api_client, mock_session
    ):
        """Benchmark authenticated GET request latency."""
        mock_session.request.return_value = MockResponse(200, {"vacancies": []})

        def run():
            return api_client.get("/vacancies")

        result = benchmark(run)
        assert "vacancies" in result

    def test_token_refresh_performance(
        self, benchmark, api_client, mock_session
    ):
        """Benchmark token refresh on 401 error."""
        # First call returns 401, second succeeds
        responses = [
            MockResponse(403, {"error": "Forbidden"}),
            MockResponse(
                200,
                {
                    "access_token": "USER_new_token",
                    "refresh_token": "REFRESH_new",
                    "expires_in": 3600,
                },
            ),
            MockResponse(200, {"vacancies": []}),
        ]
        mock_session.request.side_effect = responses

        def run():
            return api_client.get("/vacancies")

        result = benchmark(run)
        assert "vacancies" in result

    def test_pagination_performance(self, benchmark, api_client, mock_session):
        """Benchmark pagination request performance."""
        mock_session.request.return_value = MockResponse(
            200,
            {
                "items": [{"id": i} for i in range(20)],
                "pages": 5,
                "page": 0,
                "per_page": 20,
            },
        )

        def run():
            # Simulate fetching 5 pages
            all_items = []
            for page in range(5):
                response = api_client.get(
                    "/vacancies", params={"page": page, "per_page": 20}
                )
                all_items.extend(response["items"])
            return all_items

        result = benchmark(run)
        assert len(result) == 100

    def test_batch_vacancy_fetch(self, benchmark, api_client, mock_session):
        """Benchmark fetching multiple vacancies in sequence."""
        mock_session.request.return_value = MockResponse(
            200, {"id": "123", "name": "Test"}
        )

        def run():
            # Simulate fetching 50 vacancies
            vacancies = []
            for i in range(50):
                vacancies.append(api_client.get(f"/vacancies/{i}"))
            return vacancies

        result = benchmark(run)
        assert len(result) == 50


# ============================================================================
# OAuthClient Benchmarks
# ============================================================================


class TestOAuthClientBenchmarks:
    """Benchmarks for OAuth client operations."""

    def test_authorize_url_generation(self, benchmark, oauth_client):
        """Benchmark authorize URL generation."""

        def run():
            return oauth_client.authorize_url

        result = benchmark(run)
        assert "test_client" in result

    def test_token_exchange_performance(
        self, benchmark, oauth_client, mock_session
    ):
        """Benchmark token exchange performance."""
        mock_session.request.return_value = MockResponse(
            200,
            {
                "access_token": "USER_token",
                "refresh_token": "REFRESH_token",
                "expires_in": 3600,
            },
        )

        def run():
            return oauth_client.authenticate("auth_code_123")

        result = benchmark(run)
        assert result["access_token"] == "USER_token"

    def test_refresh_token_performance(
        self, benchmark, oauth_client, mock_session
    ):
        """Benchmark refresh token performance."""
        mock_session.request.return_value = MockResponse(
            200,
            {
                "access_token": "USER_new_token",
                "refresh_token": "REFRESH_new",
                "expires_in": 3600,
            },
        )

        def run():
            return oauth_client.refresh_access_token("REFRESH_token")

        result = benchmark(run)
        assert result["access_token"] == "USER_new_token"


# ============================================================================
# Connection Pooling Benchmarks
# ============================================================================


class TestConnectionPoolingBenchmarks:
    """Benchmarks for connection pooling and reuse."""

    def test_session_reuse(self, benchmark, base_client, mock_session):
        """Benchmark session reuse across requests."""
        mock_session.request.return_value = MockResponse(200, {"data": "test"})

        def run():
            # Make 20 requests reusing the same session
            for _ in range(20):
                base_client.get("/test")

        benchmark(run)

    def test_new_session_per_request(self, benchmark):
        """Benchmark creating new session per request (anti-pattern)."""

        def run():
            for _ in range(20):
                _client = BaseClient(  # noqa: F841 - measured for cost
                    base_url="https://api.test/",
                    delay=0.0,
                    timeout=30.0,
                )
                # Note: we don't actually make requests, just measure session creation

        benchmark(run)


# ============================================================================
# Rate Limiting Benchmarks
# ============================================================================


class TestRateLimitingBenchmarks:
    """Benchmarks for rate limiting behavior."""

    def test_rate_limit_delay_calculation(self, benchmark, base_client):
        """Benchmark rate limit delay calculation."""
        base_client._previous_request_time = time.monotonic() - 0.1
        base_client.delay = 0.345

        def run():
            # Access the delay calculation logic
            delay = (
                base_client.delay
                - time.monotonic()
                + base_client._previous_request_time
            )
            return max(0, delay)

        benchmark(run)

    def test_burst_request_handling(self, benchmark, base_client, mock_session):
        """Benchmark handling burst of requests."""
        mock_session.request.return_value = MockResponse(200, {"data": "test"})

        def run():
            # Simulate burst of 100 requests
            for _ in range(100):
                base_client.get("/test")

        benchmark(run)


# ============================================================================
# JSON Serialization Benchmarks
# ============================================================================


class TestSerializationBenchmarks:
    """Benchmarks for request/response serialization."""

    def test_request_param_serialization(
        self, benchmark, base_client, mock_session
    ):
        """Benchmark request parameter serialization."""
        mock_session.request.return_value = MockResponse(200, {"ok": True})

        params = {
            "text": "python developer",
            "area": 1,
            "per_page": 100,
            "page": 0,
            "experience": ["between1And3", "between3And6"],
            "employment": ["full", "part"],
            "schedule": ["fullDay", "remote"],
        }

        def run():
            return base_client.get("/vacancies", params=params)

        result = benchmark(run)
        assert result == {"ok": True}

    def test_large_response_parsing(self, benchmark, base_client, mock_session):
        """Benchmark parsing large JSON responses."""
        large_response = {
            "items": [
                {"id": i, "name": f"Vacancy {i}", "description": "x" * 1000}
                for i in range(100)
            ],
            "found": 10000,
            "pages": 100,
            "per_page": 100,
        }
        mock_session.request.return_value = MockResponse(200, large_response)

        def run():
            return base_client.get("/vacancies")

        result = benchmark(run)
        assert len(result["items"]) == 100
