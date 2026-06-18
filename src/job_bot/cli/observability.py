"""CLI-операция ``observability`` (issue #203).

A diagnostic subcommand that wires the three observability
sub-systems (tracing, JSON logging, Prometheus metrics) and runs
for a configurable amount of time. Operators use this to:

* Smoke-test the observability stack in a fresh container without
  having to launch a real daemon.
* Generate synthetic traces / metrics for end-to-end pipeline
  validation (e.g. ``otel-collector`` connectivity).
* Expose ``/metrics`` on a chosen port for a Prometheus scraper
  to discover.

Why a subcommand (not a global flag)? Two reasons:

* **Pre-subparser flags complicate dispatch.** The legacy
  ``HHApplicantTool._create_parser`` builds one subparser per
  ``BUILTIN_OPERATIONS`` entry. A global flag on the top-level
  parser would need to be threaded through every op's
  ``setup_parser`` (and its ``run``), which is invasive for what
  is essentially an opt-in infrastructure concern.
* **Smoke-testability.** A standalone ``observability`` op can
  run in CI / dev containers without a real DB or HH API
  credentials. The same wiring code is what each daemon would
  call from its own ``run()`` method (see :func:`init_observability`).

The op deliberately does *no* business work -- it's a "show me
the infra is plumbed correctly" tool. The long-running daemons
(``apply-worker``, ``telegram-bot``, ``max-bot``,
``channel-monitor``) wire the same three sub-systems in their
own ``run()`` methods.
"""

from __future__ import annotations

import argparse
import logging
import threading
import time

from job_bot.shared.observability import (
    Observability,
    configure_json_logging,
    init_observability,
    log_event,
)

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)

DEFAULT_RUNTIME_SECONDS = 30.0


class Namespace(BaseNamespace):
    """Аргументы ``observability``."""

    metrics_port: int | None
    metrics_host: str
    otel_endpoint: str | None
    log_level: str
    console_traces: bool
    runtime: float


def _build_observability(
    args: argparse.Namespace,
) -> Observability:
    """Wire the three observability sub-systems from parsed CLI args.

    Centralised in one helper so a future daemon (``apply-worker``,
    etc.) can import the same wiring code rather than copy/paste.

    Args:
        args: The argparse namespace with the ``metrics_port``,
            ``otel_endpoint``, ``log_level``, and
            ``console_traces`` fields populated.

    Returns:
        A :class:`Observability` bundle ready to ``.start()``.
    """
    # Configure JSON logging *first* so the init logs from
    # :func:`init_observability` (which prints "tracing disabled"
    # etc. on missing deps) are emitted in the canonical shape.
    configure_json_logging(level=getattr(args, "log_level", "INFO"))
    return init_observability(
        "observability-cli",
        metrics_port=getattr(args, "metrics_port", None),
        metrics_host=getattr(args, "metrics_host", "127.0.0.1"),
        otel_endpoint=getattr(args, "otel_endpoint", None),
        log_level=getattr(args, "log_level", "INFO"),
        console_traces=getattr(args, "console_traces", False),
    )


class Operation(BaseOperation):
    """Smoke-test the observability stack for ``--runtime`` seconds.

    Wires tracing + JSON logs + Prometheus metrics, emits a
    couple of demo events, and waits. ``SIGINT`` cleanly shuts
    down the stack before the process exits.
    """

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        """Add the observability-specific flags to ``parser``."""
        parser.add_argument(
            "--metrics-port",
            type=int,
            default=None,
            help=(
                "Запустить HTTP-сервер с эндпоинтом /metrics на "
                "указанном порту. Если не указан -- сервер не "
                "запускается (реестр метрик всё равно "
                "заполняется)."
            ),
        )
        parser.add_argument(
            "--metrics-host",
            type=str,
            default="127.0.0.1",
            help=(
                "Интерфейс, на котором слушает metrics-сервер. По "
                "умолчанию: 127.0.0.1 (loopback). В k8s/Docker "
                "указывайте 0.0.0.0, чтобы scraper мог достучаться."
            ),
        )
        parser.add_argument(
            "--otel-endpoint",
            type=str,
            default=None,
            help=(
                "OTLP endpoint URL (например, "
                "http://otel-collector:4317). Если не указан -- "
                "используется переменная OTEL_EXPORTER_OTLP_ENDPOINT "
                "или, при её отсутствии, console-exporter."
            ),
        )
        parser.add_argument(
            "--log-level",
            type=str,
            default="INFO",
            help="Уровень логирования (DEBUG, INFO, WARNING, ...).",
        )
        parser.add_argument(
            "--console-traces",
            action="store_true",
            help="Принудительно использовать OTel console-exporter.",
        )
        parser.add_argument(
            "--runtime",
            type=float,
            default=DEFAULT_RUNTIME_SECONDS,
            help=(
                "Сколько секунд держать процесс живым для сбора "
                "трейсов / метрик. По умолчанию: "
                f"{DEFAULT_RUNTIME_SECONDS}."
            ),
        )

    def run(self, args: argparse.Namespace) -> int:
        """Wire the stack, emit a couple of demo events, wait.

        Returns 0 on a clean shutdown, 1 if the metrics server
        fails to bind (a clear "port already in use" signal).
        """
        obs = _build_observability(args)
        try:
            obs.start()
        except OSError as exc:
            logger.exception(
                "observability: failed to bind metrics port %s: %s",
                getattr(args, "metrics_port", None),
                exc,
            )
            return 1
        log_event(
            "observability_started",
            metrics_port=getattr(args, "metrics_port", None),
            otel_endpoint=getattr(args, "otel_endpoint", None),
        )
        # Emit a root span around the "wait for runtime" body so
        # the trace pipeline has something to pick up. Each
        # second we emit a sample counter increment + a log event
        # so a Prometheus scraper / log aggregator sees the
        # stream is alive.
        runtime = float(getattr(args, "runtime", DEFAULT_RUNTIME_SECONDS))
        stop = threading.Event()
        try:
            with obs.tracer.span("observability_runtime", runtime_s=runtime):
                self._emit_demo_events(obs, runtime, stop)
        finally:
            log_event("observability_shutdown")
            obs.shutdown()
        return 0

    @staticmethod
    def _emit_demo_events(
        obs: Observability, runtime: float, stop: threading.Event
    ) -> None:
        """Emit one log event + counter increment per second for ``runtime`` s.

        Args:
            obs: The :class:`Observability` bundle (carries the
                :class:`Tracer` + :class:`Metrics` handles).
            runtime: Total number of seconds to keep the loop
                alive.
            stop: A :class:`threading.Event` the caller can set
                to break the loop early (used by the SIGINT
                handler).
        """
        # Register a demo counter so the /metrics payload isn't
        # empty.
        counter = obs.metrics.counter(
            "observability_heartbeats",
            "Number of heartbeats emitted by the observability CLI",
        )
        start = time.monotonic()
        deadline = start + runtime
        while not stop.is_set() and time.monotonic() < deadline:
            counter.inc()
            log_event("observability_heartbeat")
            # Sleep in 0.1-s slices so SIGINT is responsive.
            stop.wait(timeout=0.1)


__all__ = ("DEFAULT_RUNTIME_SECONDS", "Namespace", "Operation")
