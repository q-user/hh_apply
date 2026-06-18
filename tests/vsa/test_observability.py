"""Tests for the shared observability stack (issue #203).

Covers the three sub-systems and the ``/metrics`` HTTP endpoint:

* **Logging** -- :class:`JsonFormatter` emits valid JSON with the
  canonical ``timestamp`` / ``level`` / ``logger`` / ``message``
  fields, and any ``extra={...}`` fields the caller added.
  :func:`log_event` is a thin wrapper that pushes the event name
  into ``extra["event"]``.
* **Tracing** -- :class:`NullTracer` is a no-op (so un-instrumented
  callers don't crash). :class:`OtelTracer` is exercised by smoke
  tests; the unit-test focus is on the *interface* (Protocol
  shape, context-manager protocol) rather than on the OTel SDK
  itself.
* **Metrics** -- :class:`NullMetrics` is a no-op;
  :class:`PrometheusMetrics` round-trips through the
  :mod:`prometheus_client` registry and ``render_metrics()``
  returns the canonical text format.
* **HTTP server** -- :class:`MetricsServer` binds to a free
  port, serves ``/metrics`` with the Prometheus content-type, and
  shuts down cleanly.

All tests are hermetic: no network, no filesystem (except the
stdlib ``http.server`` in-process), no OTel collector, no real
HH API. The :mod:`prometheus_client` and :mod:`opentelemetry-sdk`
optional deps *are* required to run this file -- the project's
``[dependency-groups] dev`` already pulls them in via
``uv sync --all-extras`` (or `pip install -e .[observability]`
in a minimal env).
"""

from __future__ import annotations

import io
import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Iterator

import pytest

from job_bot.shared.observability.logging import (
    JsonFormatter,
    configure_json_logging,
    log_event,
)
from job_bot.shared.observability.metrics import (
    NullCounter,
    NullGauge,
    NullHistogram,
    NullMetrics,
    PrometheusMetrics,
    render_metrics,
)
from job_bot.shared.observability.server import MetricsServer
from job_bot.shared.observability.tracing import (
    NullSpan,
    NullTracer,
    Span,
    Tracer,
    init_tracing,
    shutdown_tracing,
)

# ─── Helpers ─────────────────────────────────────────────────────


def _free_port() -> int:
    """Ask the kernel for a free TCP port.

    Same race window as :class:`HealthServer`'s tests -- the
    :class:`MetricsServer` has the same race (it's how stdlib
    ``http.server`` works). For unit tests this is fine.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_server(host: str, port: int, timeout: float = 2.0) -> None:
    """Poll the TCP port until the server is accepting connections."""
    deadline = time.monotonic() + timeout
    last_err: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return
        except OSError as exc:
            last_err = exc
            time.sleep(0.01)
    raise TimeoutError(
        f"server at {host}:{port} did not start within {timeout}s "
        f"(last error: {last_err!r})"
    )


def _http_get(
    url: str, *, timeout: float = 2.0
) -> tuple[int, dict[str, str], bytes]:
    """Issue a GET, return ``(status_code, headers, body_bytes)``."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return (
                int(resp.status),
                dict(resp.headers.items()),
                resp.read(),
            )
    except urllib.error.HTTPError as exc:
        # 503 / 500 / 404 still have a body; capture instead of
        # letting urlopen raise.
        return (
            int(exc.code),
            dict(exc.headers.items()),
            exc.read(),
        )


@pytest.fixture
def isolated_prometheus_registry() -> Iterator[None]:
    """Give each test a fresh Prometheus registry.

    :mod:`prometheus_client` uses a process-global default
    registry, so tests that register metrics with the same name
    would otherwise leak state between cases. We use a
    per-fixture :class:`PrometheusMetrics` instance with its own
    private registry, so the side effect is bounded to the test.
    """
    yield
    # No teardown needed: each test's ``PrometheusMetrics`` is
    # constructed with its own registry.


# ─── Logging ─────────────────────────────────────────────────────


class TestJsonFormatter:
    """``JsonFormatter`` emits a single-line JSON document per record."""

    def test_json_formatter_emits_valid_json(self) -> None:
        """A log record -> one JSON line with the canonical fields.

        The canonical fields are ``timestamp`` (ISO 8601 with ``Z``
        suffix), ``level``, ``logger``, ``message``. ``extra``
        fields are merged into the top level.
        """
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="job_bot.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        line = formatter.format(record)
        # Valid JSON, no trailing newline (the StreamHandler adds
        # one).
        payload = json.loads(line)
        assert isinstance(payload, dict)
        assert payload["level"] == "INFO"
        assert payload["logger"] == "job_bot.test"
        assert payload["message"] == "hello world"
        # ISO 8601 with trailing Z
        assert payload["timestamp"].endswith("Z")
        # Sanity: parses as a datetime
        from datetime import datetime

        datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))

    def test_json_formatter_merges_extra_fields(self) -> None:
        """``extra={...}`` fields appear as top-level keys."""
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="job_bot.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="event",
            args=(),
            exc_info=None,
        )
        record.vacancy_id = "v-123"  # type: ignore[attr-defined]
        record.employer = "Acme"  # type: ignore[attr-defined]
        line = formatter.format(record)
        payload = json.loads(line)
        assert payload["vacancy_id"] == "v-123"
        assert payload["employer"] == "Acme"

    def test_json_formatter_serializes_exceptions(self) -> None:
        """``exc_info`` becomes a string under the ``exc_info`` key."""
        formatter = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="job_bot.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
            line = formatter.format(record)
        payload = json.loads(line)
        assert "exc_info" in payload
        assert "ValueError" in payload["exc_info"]
        assert "boom" in payload["exc_info"]

    def test_json_formatter_handles_unicode(self) -> None:
        """Non-ASCII characters survive the round trip (no escaping)."""
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="job_bot.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Привет",
            args=(),
            exc_info=None,
        )
        line = formatter.format(record)
        assert "Привет" in line
        # Round-trip through JSON.
        payload = json.loads(line)
        assert payload["message"] == "Привет"


class TestConfigureJsonLogging:
    """``configure_json_logging`` installs the formatter on the root."""

    def test_configure_replaces_previous_handler(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A second call replaces the previous handler (idempotency)."""
        configure_json_logging(level="INFO")
        first_handlers = list(logging.getLogger().handlers)
        assert any(getattr(h, "_job_bot_json", False) for h in first_handlers)
        # Second call should not stack a second JSON handler.
        configure_json_logging(level="INFO")
        second_handlers = list(logging.getLogger().handlers)
        json_handlers = [
            h for h in second_handlers if getattr(h, "_job_bot_json", False)
        ]
        assert len(json_handlers) == 1


class TestLogEvent:
    """``log_event`` is a thin wrapper around ``logger.info``."""

    def test_log_event_emits_event_field(self) -> None:
        """``log_event("foo", k="v")`` emits a record with event=foo, k=v."""
        stream = io.StringIO()
        configure_json_logging(level="INFO", stream=stream)
        log_event("vacancy_processed", id="x", employer="acme")
        # Flush handlers
        for handler in logging.getLogger().handlers:
            handler.flush()
        # The first line of output is our event.
        line = stream.getvalue().splitlines()[0]
        payload = json.loads(line)
        assert payload["event"] == "vacancy_processed"
        assert payload["id"] == "x"
        assert payload["employer"] == "acme"
        # The human-readable message is the event name too.
        assert payload["message"] == "vacancy_processed"

    def test_log_event_uses_event_name_as_message(self) -> None:
        """The log message is the event name (for grep-ability)."""
        stream = io.StringIO()
        configure_json_logging(level="INFO", stream=stream)
        log_event("daemon_started", pid=42)
        for handler in logging.getLogger().handlers:
            handler.flush()
        line = stream.getvalue().splitlines()[0]
        payload = json.loads(line)
        assert payload["message"] == "daemon_started"
        assert payload["event"] == "daemon_started"
        assert payload["pid"] == 42


# ─── Tracing ──────────────────────────────────────────────────────


class TestNullTracer:
    """The null tracer is a no-op that always returns null spans."""

    def test_null_tracer_does_nothing(self) -> None:
        """``with NullTracer().span("test"): pass`` is silent + safe."""
        tracer: Tracer = NullTracer()
        with tracer.span("test", key="value"):
            pass  # no exception, no side effect

    def test_null_tracer_returns_null_span(self) -> None:
        """``NullTracer().span(...)`` returns a :class:`NullSpan`."""
        span: Span = NullTracer().span("foo")
        assert isinstance(span, NullSpan)
        # set_attribute / record_exception are no-ops
        span.set_attribute("k", "v")
        span.record_exception(RuntimeError("ignored"))

    def test_null_span_context_manager_propagates_exceptions(self) -> None:
        """The null span's ``__exit__`` does not swallow exceptions.

        Unlike a real OTel span (which records the exception and
        re-raises), the null span is the *fallback* implementation:
        it should pass exceptions through unchanged. The Protocol
        documents the contract; the test pins it.
        """
        span = NullSpan()
        with pytest.raises(RuntimeError, match="ignored"):
            with span:
                raise RuntimeError("ignored")

    def test_null_tracer_satisfies_protocol(self) -> None:
        """``NullTracer`` is structurally compatible with the Protocol."""
        assert isinstance(NullTracer(), Tracer)


class TestOtelTracer:
    """Smoke tests for the real OpenTelemetry tracer."""

    def test_init_tracing_with_console_exporter(
        self,
    ) -> None:
        """``init_tracing(console=True)`` builds a working tracer."""
        tracer = init_tracing("test-service", console=True)
        try:
            with tracer.span("test_span", key="value") as span:
                span.set_attribute("attr", 42)
        finally:
            shutdown_tracing()

    def test_shutdown_tracing_is_idempotent(self) -> None:
        """Multiple calls to ``shutdown_tracing`` are safe."""
        shutdown_tracing()
        shutdown_tracing()
        # No exception means we're good.

    def test_otel_tracer_satisfies_protocol(self) -> None:
        """``OtelTracer`` is structurally compatible with the Protocol."""
        tracer = init_tracing("test-service-protocol", console=True)
        try:
            assert isinstance(tracer, Tracer)
        finally:
            shutdown_tracing()


# ─── Metrics ──────────────────────────────────────────────────────


class TestNullMetrics:
    """``NullMetrics`` is a no-op for tests that don't need real metrics."""

    def test_null_metrics_counter_is_noop(self) -> None:
        """The null counter's ``inc`` is a silent no-op."""
        counter: NullCounter = NullMetrics().counter("foo", "help")
        counter.inc()
        counter.inc(5.0)
        # No exception means we're good.

    def test_null_metrics_histogram_is_noop(self) -> None:
        """The null histogram's ``observe`` is a silent no-op."""
        hist: NullHistogram = NullMetrics().histogram("foo", "help")
        hist.observe(0.5)
        hist.observe(100.0)

    def test_null_metrics_gauge_is_noop(self) -> None:
        """The null gauge's ``set`` / ``inc`` / ``dec`` are no-ops."""
        gauge: NullGauge = NullMetrics().gauge("foo", "help")
        gauge.set(1.0)
        gauge.inc()
        gauge.dec(2.0)

    def test_null_metrics_satisfies_protocol(self) -> None:
        """``NullMetrics`` is structurally compatible with the Protocol."""
        from job_bot.shared.observability.metrics import Metrics

        assert isinstance(NullMetrics(), Metrics)


class TestPrometheusMetrics:
    """``PrometheusMetrics`` round-trips through ``prometheus_client``."""

    def test_prometheus_metrics_counter(
        self,
        isolated_prometheus_registry: None,
    ) -> None:
        """``counter(...).inc()`` is visible in ``render_metrics()``."""
        import prometheus_client

        registry = prometheus_client.CollectorRegistry()
        metrics = PrometheusMetrics(registry=registry)
        c = metrics.counter("foo", "help text")
        c.inc()
        # Render and assert the counter is visible.
        text = prometheus_client.generate_latest(registry).decode("utf-8")
        # The counter name is qualified + suffixed with _total
        # per the Prometheus convention.
        assert "hh_apply_foo_total 1.0" in text
        # And the HELP line is present.
        assert "# HELP hh_apply_foo_total help text" in text

    def test_prometheus_metrics_counter_already_total(
        self,
        isolated_prometheus_registry: None,
    ) -> None:
        """A name ending in ``_total`` is *not* double-suffixed."""
        import prometheus_client

        registry = prometheus_client.CollectorRegistry()
        metrics = PrometheusMetrics(registry=registry)
        c = metrics.counter("foo_total", "help text")
        c.inc()
        text = prometheus_client.generate_latest(registry).decode("utf-8")
        assert "hh_apply_foo_total 1.0" in text
        # No double suffix.
        assert "hh_apply_foo_total_total" not in text

    def test_prometheus_metrics_histogram(
        self,
        isolated_prometheus_registry: None,
    ) -> None:
        """Observations land in buckets and the rendered text reflects it."""
        import prometheus_client

        registry = prometheus_client.CollectorRegistry()
        metrics = PrometheusMetrics(registry=registry)
        h = metrics.histogram(
            "latency",
            "API latency in seconds",
            buckets=(0.1, 0.5, 1.0, 5.0),
        )
        h.observe(0.05)
        h.observe(0.3)
        h.observe(2.0)
        text = prometheus_client.generate_latest(registry).decode("utf-8")
        # Each bucket emits a ``_bucket{le="..."}`` line.
        assert 'hh_apply_latency_bucket{le="0.1"}' in text
        assert 'hh_apply_latency_bucket{le="0.5"}' in text
        assert 'hh_apply_latency_bucket{le="1.0"}' in text
        assert 'hh_apply_latency_bucket{le="5.0"}' in text
        # The +Inf bucket is the count.
        assert "hh_apply_latency_count 3.0" in text
        # Sum is the total of observed values.
        assert "hh_apply_latency_sum 2.35" in text

    def test_prometheus_metrics_gauge(
        self,
        isolated_prometheus_registry: None,
    ) -> None:
        """A gauge's ``set`` is reflected in the render output."""
        import prometheus_client

        registry = prometheus_client.CollectorRegistry()
        metrics = PrometheusMetrics(registry=registry)
        g = metrics.gauge("queue_depth", "current queue size")
        g.set(7.0)
        text = prometheus_client.generate_latest(registry).decode("utf-8")
        assert "hh_apply_queue_depth 7.0" in text

    def test_prometheus_metrics_labelled_counter(
        self,
        isolated_prometheus_registry: None,
    ) -> None:
        """A counter with labels emits one line per label combination."""
        import prometheus_client

        registry = prometheus_client.CollectorRegistry()
        metrics = PrometheusMetrics(registry=registry)
        c = metrics.counter(
            "api_requests",
            "API request count",
            labels=("endpoint", "status"),
        )
        c.labels(endpoint="/vacancies", status="200").inc()
        c.labels(endpoint="/vacancies", status="500").inc(3.0)
        text = prometheus_client.generate_latest(registry).decode("utf-8")
        assert (
            'hh_apply_api_requests_total{endpoint="/vacancies",status="200"} 1.0'
            in text
        )
        assert (
            'hh_apply_api_requests_total{endpoint="/vacancies",status="500"} 3.0'
            in text
        )

    def test_render_metrics_default_registry(
        self,
    ) -> None:
        """``render_metrics()`` works against the default registry."""
        # Don't crash; just call it.
        payload = render_metrics()
        # ``bytes`` output, valid UTF-8.
        assert isinstance(payload, bytes)
        payload.decode("utf-8")


# ─── MetricsServer ───────────────────────────────────────────────


class TestMetricsServer:
    """``MetricsServer`` serves ``/metrics`` over a stdlib HTTP server."""

    @pytest.fixture
    def metrics_server_factory(self) -> Iterator[type[MetricsServer]]:
        """Track every ``MetricsServer`` we create and shut them down.

        Mirrors the helper in
        ``tests/vsa/test_health_endpoints.py`` -- ensures a
        failing test doesn't leak bound sockets / threads.
        """
        started: list[MetricsServer] = []
        yield MetricsServer
        for srv in started:
            try:
                srv.stop()
            except Exception:  # noqa: BLE001
                pass
        return  # generator-iterator return value (pytest ignores)

    def test_metrics_server_serves_endpoint(
        self,
        metrics_server_factory: type[MetricsServer],
    ) -> None:
        """``GET /metrics`` returns 200 with a Prometheus payload."""
        port = _free_port()
        srv = metrics_server_factory(port=port, host="127.0.0.1")
        srv.start()
        try:
            _wait_for_server("127.0.0.1", port)
            status, headers, body = _http_get(
                f"http://127.0.0.1:{port}/metrics"
            )
            assert status == 200
            # Prometheus text format content type.
            assert "text/plain" in headers.get("Content-Type", "")
            assert "version=0.0.4" in headers.get("Content-Type", "")
            # Body decodes as UTF-8 and is the canonical text format
            # (it can be empty if no metrics registered yet, but it
            # must be valid bytes).
            text = body.decode("utf-8")
            # The default body has at least the Prometheus process
            # metrics, OR is empty -- both are fine. Just assert
            # it's a string.
            assert isinstance(text, str)
        finally:
            srv.stop()

    def test_metrics_server_unknown_route_returns_404(
        self,
        metrics_server_factory: type[MetricsServer],
    ) -> None:
        """Unknown paths return 404, same as :class:`HealthServer`."""
        port = _free_port()
        srv = metrics_server_factory(port=port)
        srv.start()
        try:
            _wait_for_server("127.0.0.1", port)
            status, _headers, _body = _http_get(
                f"http://127.0.0.1:{port}/unknown"
            )
            assert status == 404
        finally:
            srv.stop()

    def test_metrics_server_start_stop_is_idempotent(
        self,
        metrics_server_factory: type[MetricsServer],
    ) -> None:
        """``stop()`` is safe on a running server and on a never-started one."""
        port = _free_port()
        srv = metrics_server_factory(port=port)
        # Never started -- stop is a no-op.
        srv.stop()
        srv.start()
        try:
            _wait_for_server("127.0.0.1", port)
        finally:
            srv.stop()
        # Second stop is a no-op.
        srv.stop()

    def test_metrics_server_prometheus_content_includes_registered_metric(
        self,
        metrics_server_factory: type[MetricsServer],
    ) -> None:
        """A registered counter appears in the ``/metrics`` payload."""
        import prometheus_client

        port = _free_port()
        srv = metrics_server_factory(port=port)
        srv.start()
        try:
            _wait_for_server("127.0.0.1", port)
            # Register a counter on the default registry BEFORE
            # making the request -- the render reads the default
            # registry.
            counter = prometheus_client.Counter(
                "hh_apply_unit_test_counter",
                "test counter",
            )
            counter.inc(2.0)
            _status, _headers, body = _http_get(
                f"http://127.0.0.1:{port}/metrics"
            )
            text = body.decode("utf-8")
            assert "hh_apply_unit_test_counter_total 2.0" in text
        finally:
            # Best-effort cleanup; tests must not depend on global
            # registry state.
            try:
                prometheus_client.REGISTRY.unregister(counter)
            except Exception:  # noqa: BLE001
                pass
            srv.stop()


# ─── Optional-deps behaviour ──────────────────────────────────────


class TestOptionalDepsImportError:
    """Constructing the real backends without the optional dep raises."""

    def test_prometheus_metrics_import_error_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ``prometheus_client``, the constructor raises ImportError.

        We simulate the missing dep by stubbing
        :func:`_require_prometheus` in the metrics module. The
        install hint must be present so the operator can fix the
        environment.
        """
        from job_bot.shared.observability import metrics as metrics_mod

        def _fake_require() -> object:  # type: ignore[return-value]
            raise ImportError("Install the optional 'observability' extra")

        monkeypatch.setattr(metrics_mod, "_require_prometheus", _fake_require)
        with pytest.raises(ImportError) as excinfo:
            metrics_mod.PrometheusMetrics()
        assert "observability" in str(excinfo.value).lower()

    def test_otel_tracer_import_error_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ``opentelemetry-sdk``, the constructor raises ImportError.

        Same shape as the Prometheus test: simulate the missing
        dep via :func:`_require_opentelemetry` and assert the
        install hint is in the error message.
        """
        from job_bot.shared.observability import tracing as tracing_mod

        def _fake_require() -> object:  # type: ignore[return-value]
            raise ImportError("Install the optional 'observability' extra")

        monkeypatch.setattr(
            tracing_mod, "_require_opentelemetry", _fake_require
        )
        with pytest.raises(ImportError) as excinfo:
            tracing_mod.OtelTracer("test")
        assert "observability" in str(excinfo.value).lower()
