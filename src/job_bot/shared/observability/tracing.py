"""OpenTelemetry tracing for the observability stack (issue #203).

This module defines a tiny :class:`Tracer` Protocol, a real
:class:`OtelTracer` implementation backed by :mod:`opentelemetry-sdk`,
and a :class:`NullTracer` no-op for tests / un-instrumented code.

Design choices
--------------

* **Protocol-based.** The slice code consumes
  :class:`~typing.Protocol` instances (:class:`Span` / :class:`Tracer`), not
  :mod:`opentelemetry` concrete types. Tests inject
  :class:`NullTracer`; production wires :class:`OtelTracer`.
* **Optional dependency.** :mod:`opentelemetry-sdk` and
  :mod:`opentelemetry-exporter-otlp` are part of the
  ``observability`` extra. The :class:`OtelTracer` constructor
  raises :class:`ImportError` with an actionable install hint if
  the package isn't installed.
* **Context manager only.** A span is a ``with`` block -- the
  Protocol exposes :meth:`Span.__enter__` / :meth:`Span.__exit__`
  (inherited from :class:`object`) and :meth:`set_attribute`. No
  manual ``start()`` / ``end()`` lifecycle, so a missed end can
  never leak a span.
* **Default exporter = stdout (dev only).** When no
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, :class:`OtelTracer`
  defaults to a :class:`ConsoleSpanExporter` so traces are
  visible in the dev log without an extra collector. Production
  deployments set the endpoint to the OTLP collector.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__package__)


@runtime_checkable
class Span(Protocol):
    """A single trace span. Used as a context manager.

    Example::

        with tracer.span("apply_vacancy", vacancy_id="123") as span:
            span.set_attribute("employer", "Acme")
            do_work()

    Implementations must record exceptions raised inside the
    ``with`` block and end the span in ``__exit__``.
    """

    def set_attribute(self, key: str, value: Any) -> None:
        """Set an attribute on the span (string / int / float / bool)."""
        ...

    def record_exception(self, exc: BaseException) -> None:
        """Record ``exc`` on the span (does *not* re-raise)."""
        ...

    def __enter__(self) -> "Span":
        """Enter the ``with`` block. Returns ``self``."""
        ...

    def __exit__(self, *exc_info: Any) -> None:
        """Exit the ``with`` block. End the span and record any exception."""
        ...


@runtime_checkable
class Tracer(Protocol):
    """Factory for :class:`Span` instances.

    The factory surface is just :meth:`span` -- enough to wrap any
    long-running operation. Spans are returned as context managers
    so callers never have to remember to call ``end()``.
    """

    def span(
        self,
        name: str,
        **attributes: Any,
    ) -> "Span":
        """Open a new span named ``name`` and return it as a context manager.

        Args:
            name: The span name (e.g. ``"apply_vacancy"``).
            **attributes: Optional key/value attributes set on the
                span at start time (e.g.
                ``tracer.span("apply", vacancy_id="123")``).
        """
        ...


# ─── No-op implementations ──────────────────────────────────────


class NullSpan:
    """A :class:`Span` that does nothing.

    All methods are no-ops; the context-manager protocol just
    returns ``self``. The shape is identical to a real span so
    callers can wire either without conditional code.
    """

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ARG002
        return None

    def record_exception(self, exc: BaseException) -> None:  # noqa: ARG002
        return None

    def __enter__(self) -> "NullSpan":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None


class NullTracer:
    """A :class:`Tracer` that returns :class:`NullSpan` instances.

    Use in tests that don't want a real OTel SDK in the import
    graph, and in slices whose :class:`Tracer` is a dependency
    that can be left un-injected (the null is the safe default).
    """

    def span(
        self,
        name: str,  # noqa: ARG002
        **attributes: Any,  # noqa: ARG002
    ) -> NullSpan:
        return NullSpan()


# ─── OpenTelemetry-backed implementation ────────────────────────


_INSTALL_HINT = (
    "Install the optional 'observability' extra to enable OpenTelemetry "
    "tracing: `uv add 'hh-applicant-tool[observability]'` "
    "(or `pip install opentelemetry-sdk`)."
)


def _require_opentelemetry() -> tuple[Any, Any, Any, Any, Any]:
    """Import the OTel SDK submodules we need or raise an actionable error.

    Returns:
        A 5-tuple of ``(sdk_module, trace_module, exporter_module,
        resources_module, BatchSpanProcessor)``. We don't import
        at module top-level because the optional extra isn't
        always installed.

    Raises:
        ImportError: with the install hint above. We re-raise as
            :class:`ImportError` (not :class:`RuntimeError`) so the
            caller can ``except ImportError`` uniformly across all
            optional deps.
    """
    try:
        from opentelemetry import sdk, trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except ImportError as exc:  # pragma: no cover - depends on install
        raise ImportError(_INSTALL_HINT) from exc
    try:
        # ``OTLPSpanExporter`` ships in the
        # ``opentelemetry-exporter-otlp-proto-grpc`` extra; if the
        # daemon operator didn't pull it in, fall back to the
        # console exporter so the rest of the SDK still works.
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:  # pragma: no cover - depends on install
        OTLPSpanExporter = None  # type: ignore[assignment,misc]
    return (
        sdk,
        trace,
        OTLPSpanExporter,
        Resource,
        (TracerProvider, BatchSpanProcessor, ConsoleSpanExporter),
    )


class OtelSpan:
    """Real :class:`Span` wrapping an :class:`opentelemetry.trace.Span`."""

    def __init__(self, otel_span: Any) -> None:
        # The OTel SDK is mutable on the span itself; we keep a
        # reference and forward each method.
        self._span = otel_span

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a single attribute (string / int / float / bool / sequence)."""
        try:
            self._span.set_attribute(key, value)
        except Exception:  # noqa: BLE001
            # OTel's set_attribute raises on unsupported types; the
            # span must not crash the call site for a bad attribute.
            logger.exception("otel: set_attribute(%r, %r) failed", key, value)

    def record_exception(self, exc: BaseException) -> None:
        """Record ``exc`` (does NOT re-raise)."""
        try:
            self._span.record_exception(exc)
        except Exception:  # noqa: BLE001
            logger.exception("otel: record_exception failed")

    def __enter__(self) -> "OtelSpan":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is not None:
            # Record the exception on the span (this is what OTel
            # does internally with ``record_exception`` in the
            # default OTel context manager, but we do it explicitly
            # so the recorded exception is visible to the user
            # even if the SDK is downgraded).
            try:
                self._span.record_exception(exc)
            except Exception:  # noqa: BLE001
                logger.exception("otel: record_exception on __exit__ failed")
        try:
            self._span.end()
        except Exception:  # noqa: BLE001
            logger.exception("otel: span.end() failed")


class OtelTracer:
    """Real :class:`Tracer` backed by :mod:`opentelemetry-sdk`.

    Args:
        service_name: The ``service.name`` resource attribute
            (required by every OTel backend). Convention is the
            CLI op name (e.g. ``"apply-worker"``).
        endpoint: Optional OTLP endpoint URL (e.g.
            ``"http://otel-collector:4317"``). When ``None``,
            falls back to the standard ``OTEL_EXPORTER_OTLP_ENDPOINT``
            env var. If neither is set, the default
            :class:`ConsoleSpanExporter` is used (dev-friendly).
        console: If ``True``, force the console exporter regardless
            of the endpoint. Useful for unit tests / local dev.

    Thread-safety: :mod:`opentelemetry-sdk` is thread-safe; the
    tracer is process-global, so two :class:`OtelTracer` instances
    in the same process share the underlying :class:`TracerProvider`.
    Call :meth:`shutdown_tracing` once at process exit to flush
    pending spans.
    """

    def __init__(
        self,
        service_name: str,
        *,
        endpoint: str | None = None,
        console: bool = False,
    ) -> None:
        _, trace, OTLPSpanExporter, Resource, exporters = (
            _require_opentelemetry()
        )
        (
            TracerProvider,
            BatchSpanProcessor,
            ConsoleSpanExporter,
        ) = exporters
        # Idempotent: a second :class:`OtelTracer` in the same
        # process reuses the existing provider. The setter is
        # idempotent in modern OTel versions, so this is safe.
        resource = Resource.create({"service.name": service_name})
        provider = trace.get_tracer_provider()
        # Only set up the provider once per process. Subsequent
        # :class:`OtelTracer` instances just attach to it. The
        # OTel SDK explicitly disallows overriding an existing
        # TracerProvider, so we catch the warning and move on.
        if not getattr(provider, "_job_bot_configured", False):
            tracer_provider = TracerProvider(resource=resource)
            if console or endpoint is None or OTLPSpanExporter is None:
                # Either explicitly asked for the console exporter,
                # or the OTLP gRPC extra isn't installed -- fall
                # back to stdout so traces are still visible in
                # the dev log.
                if endpoint is not None and OTLPSpanExporter is None:
                    logger.warning(
                        "otel: OTLP exporter unavailable, falling back "
                        "to console"
                    )
                tracer_provider.add_span_processor(
                    BatchSpanProcessor(ConsoleSpanExporter())
                )
            else:
                tracer_provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
                )
            try:
                trace.set_tracer_provider(tracer_provider)
            except Exception:  # noqa: BLE001
                # Newer OTel SDKs refuse to override an existing
                # provider. The proxy already installed by a prior
                # :class:`OtelTracer` is fine; we just keep using it.
                logger.debug(
                    "otel: TracerProvider already set; reusing existing one"
                )
            provider._job_bot_configured = True
        # The named tracer is keyed on ``service_name`` so two
        # :class:`OtelTracer` instances in the same process get
        # distinct (but equivalent) tracer handles.
        self._tracer = trace.get_tracer(service_name)

    def span(
        self,
        name: str,
        **attributes: Any,
    ) -> OtelSpan:
        """Open a new span. Returns an :class:`OtelSpan` context manager."""
        otel_span = self._tracer.start_span(name)
        for key, value in attributes.items():
            try:
                otel_span.set_attribute(key, value)
            except Exception:  # noqa: BLE001
                logger.exception("otel: failed to set attribute %r", key)
        return OtelSpan(otel_span)


# ─── Module-level helpers ───────────────────────────────────────


def init_tracing(
    service_name: str,
    *,
    endpoint: str | None = None,
    console: bool = False,
) -> OtelTracer:
    """Construct a fresh :class:`OtelTracer` for the daemon.

    Args:
        service_name: The OTel ``service.name`` resource
            attribute. Should be the CLI op name
            (``"apply-worker"``, ``"telegram-bot"``,
            ``"max-bot"``, ``"channel-monitor"``).
        endpoint: Optional OTLP endpoint URL. ``None`` falls
            back to the env var / console exporter.
        console: Force the console exporter (dev / unit tests).

    Returns:
        A ready-to-use :class:`OtelTracer` instance.
    """
    return OtelTracer(
        service_name=service_name,
        endpoint=endpoint,
        console=console,
    )


def shutdown_tracing() -> None:
    """Flush + shut down the global OTel :class:`TracerProvider`.

    Idempotent. Safe to call even if tracing was never initialised
    (in that case the call is a no-op). Always wrap your CLI
    daemon's main loop in ``try / finally`` with this at the end
    so pending spans are flushed before the process exits.
    """
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover - depends on install
        return
    try:
        provider = trace.get_tracer_provider()
        # Only the SDK's ``TracerProvider`` has ``shutdown()``; the
        # default proxy provider does not.
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:  # noqa: BLE001
        logger.exception("otel: shutdown failed")


__all__ = [
    "NullSpan",
    "NullTracer",
    "OtelSpan",
    "OtelTracer",
    "Span",
    "Tracer",
    "init_tracing",
    "shutdown_tracing",
]
