"""Shared observability primitives (issue #203).

This package bundles the three cross-cutting observability
sub-systems Phase E requires:

* **Tracing** -- :mod:`.tracing` exports a :class:`Tracer`
  :class:`~typing.Protocol` with two implementations:
  :class:`OtelTracer` (real OpenTelemetry SDK, optional dep) and
  :class:`NullTracer` (no-op, used in tests and un-instrumented
  callers).
* **Logging** -- :mod:`.logging` exports a :class:`JsonFormatter`
  (stdlib :class:`logging.Formatter` subclass that emits one JSON
  document per log record) and a :func:`log_event` helper for
  structured ``extra={...}``-style calls.
* **Metrics** -- :mod:`.metrics` exports a :class:`Metrics`
  :class:`~typing.Protocol` with two implementations:
  :class:`PrometheusMetrics` (real ``prometheus_client``, optional
  dep) and :class:`NullMetrics` (no-op).

A :class:`MetricsServer` (in :mod:`.server`) ties the metrics
sub-system to a ``/metrics`` HTTP endpoint, mirroring the design
of :class:`HealthServer` in :mod:`job_bot.shared.health`.

The three sub-systems are deliberately *orthogonal* -- a caller can
use tracing without metrics, JSON logs without tracing, etc. The
common entry point is :func:`init_observability`, which wires all
three with a single call (each sub-system's optional dep is
imported lazily so the call is best-effort).

Typical CLI daemon usage::

    from job_bot.shared.observability import init_observability

    obs = init_observability("apply-worker")
    obs.start()  # starts the /metrics HTTP server (no-op if --metrics-port is None)
    try:
        with obs.tracer.span("apply_worker_loop") as span:
            ...  # main daemon loop
    finally:
        obs.shutdown()  # flushes OTel + stops the HTTP server

Why a single package? Because all three sub-systems share the same
"opt-in via the ``observability`` extra" pattern, and a single
import keeps the daemon entry points short.
"""

from __future__ import annotations

import logging as _logging
from dataclasses import dataclass
from typing import Any

from job_bot.shared.observability.logging import (
    JsonFormatter,
    configure_json_logging,
    log_event,
)
from job_bot.shared.observability.metrics import (
    Counter,
    Gauge,
    Histogram,
    Metrics,
    NullCounter,
    NullGauge,
    NullHistogram,
    NullMetrics,
    PrometheusMetrics,
    init_metrics,
    render_metrics,
)
from job_bot.shared.observability.server import (
    DEFAULT_HOST as METRICS_DEFAULT_HOST,
    MetricsServer,
)
from job_bot.shared.observability.tracing import (
    NullSpan,
    NullTracer,
    OtelSpan,
    OtelTracer,
    Span,
    Tracer,
    init_tracing,
    shutdown_tracing,
)

logger = _logging.getLogger("job_bot.shared.observability")


# ─── Top-level wiring ──────────────────────────────────────────


@dataclass
class Observability:
    """Bundle of the three sub-systems + the :class:`MetricsServer`.

    Returned by :func:`init_observability`. Holds the live
    :class:`Tracer`, :class:`Metrics`, and :class:`MetricsServer`
    handles for the lifetime of the daemon; ``shutdown()`` flushes
    OTel and stops the HTTP server.

    Attributes:
        tracer: The :class:`Tracer` (real or null, depending on
            whether the optional dep is installed). Always non-``None``.
        metrics: The :class:`Metrics` (real or null). Always
            non-``None``.
        metrics_server: The :class:`MetricsServer` (only started
            when ``metrics_port`` was provided). ``None`` when no
            port was given.
    """

    tracer: Tracer
    metrics: Metrics
    metrics_server: MetricsServer | None

    def start(self) -> None:
        """Start the :class:`MetricsServer` (if configured).

        The OTel tracer and the Prometheus registry don't need an
        explicit start -- the tracer is ready to use after
        :func:`init_tracing`, and the registry is process-global.
        """
        if self.metrics_server is not None:
            self.metrics_server.start()

    def shutdown(self) -> None:
        """Tear down all three sub-systems.

        Idempotent: a second call is a safe no-op. Always wrap
        the daemon's main loop in ``try / finally`` with this at
        the end so pending OTel spans are flushed and the HTTP
        server is released.
        """
        if self.metrics_server is not None:
            self.metrics_server.stop()
        shutdown_tracing()


def init_observability(
    service_name: str,
    *,
    metrics_port: int | None = None,
    metrics_host: str = METRICS_DEFAULT_HOST,
    otel_endpoint: str | None = None,
    log_level: str = "INFO",
    console_traces: bool = False,
) -> Observability:
    """Wire the three observability sub-systems for a daemon.

    Each sub-system is *best-effort*: if the optional dep isn't
    installed, the real implementation is replaced with the null
    implementation and a warning is logged. The function never
    raises on a missing dep.

    Args:
        service_name: The OTel ``service.name`` (and the
            :class:`MetricsServer`'s identifying label). The CLI
            op name (``"apply-worker"``, ``"telegram-bot"``,
            ``"max-bot"``, ``"channel-monitor"``) is the
            convention.
        metrics_port: TCP port for the ``/metrics`` HTTP endpoint.
            ``None`` (the default) means no server is started --
            the metrics registry is still populated, but there's
            no way to scrape it.
        metrics_host: Interface to bind the metrics server. See
            :class:`MetricsServer`.
        otel_endpoint: OTLP endpoint URL. ``None`` falls back to
            the standard ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var.
            If neither is set, the console exporter is used.
        log_level: Root-logger level passed to
            :func:`configure_json_logging`.
        console_traces: Force the OTel console exporter. Useful
            for unit tests / local dev.

    Returns:
        A :class:`Observability` bundle. Call :meth:`start` once
        the daemon is ready to accept traffic, and
        :meth:`shutdown` in a ``finally`` block.
    """
    # ── tracing ──────────────────────────────────────────────
    try:
        tracer: Tracer = init_tracing(
            service_name=service_name,
            endpoint=otel_endpoint,
            console=console_traces,
        )
    except ImportError as exc:
        logger.warning(
            "observability: tracing disabled -- %s",
            exc,
        )
        tracer = NullTracer()

    # ── metrics ──────────────────────────────────────────────
    try:
        metrics: Metrics = init_metrics()
    except ImportError as exc:
        logger.warning(
            "observability: metrics disabled -- %s",
            exc,
        )
        metrics = NullMetrics()

    # ── structured logging ──────────────────────────────────
    configure_json_logging(level=log_level)

    # ── metrics server (only if a port was provided) ────────
    server: MetricsServer | None = None
    if metrics_port is not None:
        server = MetricsServer(port=metrics_port, host=metrics_host)

    return Observability(
        tracer=tracer,
        metrics=metrics,
        metrics_server=server,
    )


__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "JsonFormatter",
    "METRICS_DEFAULT_HOST",
    "Metrics",
    "MetricsServer",
    "NullCounter",
    "NullGauge",
    "NullHistogram",
    "NullMetrics",
    "NullSpan",
    "NullTracer",
    "Observability",
    "OtelSpan",
    "OtelTracer",
    "Span",
    "Tracer",
    "configure_json_logging",
    "init_metrics",
    "init_observability",
    "init_tracing",
    "log_event",
    "render_metrics",
    "shutdown_tracing",
]
