"""Prometheus metrics for the observability stack (issue #203).

This module defines a :class:`Metrics` Protocol, a real
:class:`PrometheusMetrics` implementation backed by
:mod:`prometheus_client`, and a :class:`NullMetrics` no-op for tests
that don't want to touch the real registry.

Design choices
--------------

* **Protocol-based.** The slice code consumes
  :class:`~typing.Protocol` instances (:class:`Counter` / :class:`Histogram` /
  :class:`Gauge`), not :class:`prometheus_client` concrete types.
  Tests inject :class:`NullMetrics`; production wires
  :class:`PrometheusMetrics`.
* **Single global registry.** :mod:`prometheus_client` uses a
  module-level default registry; we delegate to it so the
  ``/metrics`` HTTP endpoint can call :func:`render_metrics` and get
  the canonical text-format output. The :class:`PrometheusMetrics`
  constructor *resets* the default registry on demand (``reset=True``)
  so tests start from a known state -- the default behaviour is to
  *append* to the existing registry, which is what production
  daemons want when more than one component is wired in the same
  process.
* **Optional dependency.** :mod:`prometheus_client` is part of the
  ``observability`` extra. The :class:`PrometheusMetrics` constructor
  raises :class:`ImportError` with an actionable message if the
  package isn't installed; the :class:`NullMetrics` / Protocol / type
  aliases do not.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, cast, runtime_checkable

logger = logging.getLogger(__package__)


@runtime_checkable
class Counter(Protocol):
    """Counter -- monotonic, only ever increments.

    The Prometheus convention is that a counter's name ends in
    ``_total``; the :class:`PrometheusMetrics` adapter adds the
    suffix automatically so callers can write
    ``metrics.counter("hh_apply_vacancies_processed", ...)`` without
    repeating the convention by hand.
    """

    def inc(self, amount: float = 1.0) -> None:
        """Increment the counter by ``amount`` (default 1.0)."""
        ...


@runtime_checkable
class Histogram(Protocol):
    """Histogram -- bucketed distribution of observed values.

    Histograms are the right shape for latencies and durations:
    each ``observe(value)`` lands in a bucket, the ``+Inf`` bucket
    is the total count, and ``_sum`` is the running sum (Prometheus
    derives the average server-side).
    """

    def observe(self, value: float) -> None:
        """Record ``value`` in the histogram's bucket set."""
        ...


@runtime_checkable
class Gauge(Protocol):
    """Gauge -- a value that goes up and down.

    Used for "what is the current value of X?" -- e.g.
    ``hh_apply_runtime_seconds`` (process uptime) or
    ``hh_apply_queue_depth`` (number of pending jobs).
    """

    def set(self, value: float) -> None:
        """Set the gauge to ``value``."""
        ...

    def inc(self, amount: float = 1.0) -> None:
        """Add ``amount`` to the gauge (default 1.0)."""
        ...

    def dec(self, amount: float = 1.0) -> None:
        """Subtract ``amount`` from the gauge (default 1.0)."""
        ...


@runtime_checkable
class Metrics(Protocol):
    """Factory interface for the three metric primitives.

    Each call to :meth:`counter` / :meth:`histogram` / :meth:`gauge`
    returns a *handle* that the caller can use from any thread. The
    same ``name`` always returns the same handle (the underlying
    :mod:`prometheus_client` registry deduplicates by name).
    """

    def counter(
        self,
        name: str,
        help: str,
        *,
        labels: tuple[str, ...] = (),
    ) -> Counter:
        """Return a counter handle for ``name``.

        Args:
            name: Metric name. The :class:`PrometheusMetrics`
                adapter appends ``_total`` if it isn't already
                there, so callers can pass either
                ``"vacancies_processed"`` or
                ``"vacancies_processed_total"``.
            help: Human-readable description (Prometheus
                ``# HELP`` line).
            labels: Optional label-name tuple. Empty tuple means a
                scalar (no labels); pass ``("endpoint", "status")``
                for a labelled counter.
        """
        ...

    def histogram(
        self,
        name: str,
        help: str,
        *,
        labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
    ) -> Histogram:
        """Return a histogram handle for ``name``.

        Args:
            name: Metric name. The default bucket set is the
                Prometheus standard
                ``(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10)``
                which fits the HH API latency profile.
            help: Human-readable description.
            labels: Optional label-name tuple.
            buckets: Override the default bucket set. Useful for
                sub-second latencies (the defaults start at 5 ms).
        """
        ...

    def gauge(
        self,
        name: str,
        help: str,
        *,
        labels: tuple[str, ...] = (),
    ) -> Gauge:
        """Return a gauge handle for ``name``."""
        ...


class NullCounter:
    """No-op counter for tests that don't want the real registry."""

    def inc(self, amount: float = 1.0) -> None:  # noqa: ARG002 -- signature parity
        return None


class NullHistogram:
    """No-op histogram for tests."""

    def observe(self, value: float) -> None:  # noqa: ARG002 -- signature parity
        return None


class NullGauge:
    """No-op gauge for tests."""

    def set(self, value: float) -> None:  # noqa: ARG002 -- signature parity
        return None

    def inc(self, amount: float = 1.0) -> None:  # noqa: ARG002
        return None

    def dec(self, amount: float = 1.0) -> None:  # noqa: ARG002
        return None


class NullMetrics:
    """A :class:`Metrics` whose handles are all no-ops.

    Use this in tests that exercise slice logic without wanting to
    pollute the real Prometheus registry (which is process-global
    and persists between test cases). A single instance can be
    shared across threads safely because the handles are stateless.
    """

    def counter(
        self,
        name: str,  # noqa: ARG002 -- signature parity
        help: str,  # noqa: ARG002
        *,
        labels: tuple[str, ...] = (),  # noqa: ARG002
    ) -> NullCounter:
        return NullCounter()

    def histogram(
        self,
        name: str,  # noqa: ARG002
        help: str,  # noqa: ARG002
        *,
        labels: tuple[str, ...] = (),  # noqa: ARG002
        buckets: tuple[float, ...] | None = None,  # noqa: ARG002
    ) -> NullHistogram:
        return NullHistogram()

    def gauge(
        self,
        name: str,  # noqa: ARG002
        help: str,  # noqa: ARG002
        *,
        labels: tuple[str, ...] = (),  # noqa: ARG002
    ) -> NullGauge:
        return NullGauge()


# ─── Prometheus-backed implementation ───────────────────────────


_INSTALL_HINT = (
    "Install the optional 'observability' extra to enable Prometheus "
    "metrics: `uv add 'hh-applicant-tool[observability]'` "
    "(or `pip install prometheus-client`)."
)


def _require_prometheus() -> Any:
    """Import :mod:`prometheus_client` or raise an actionable error.

    Returns:
        The :mod:`prometheus_client` module.

    Raises:
        ImportError: with the install hint above. We re-raise as
            :class:`ImportError` (not :class:`RuntimeError`) so the
            caller can `except ImportError` uniformly across all
            optional deps.
    """
    try:
        import prometheus_client  # noqa: F401 -- checked below
    except ImportError as exc:  # pragma: no cover - depends on install
        raise ImportError(_INSTALL_HINT) from exc
    import prometheus_client as _pc

    return _pc


class PrometheusMetrics:
    """Real :class:`Metrics` backed by :mod:`prometheus_client`.

    Args:
        namespace: The Prometheus metric-name prefix (default
            ``"hh_apply"``). All metric names registered through
            this instance are prefixed with ``"<namespace>_"`` --
            so ``counter("vacancies_processed", ...)`` becomes
            ``hh_apply_vacancies_processed_total`` in the output.
        registry: Optional :class:`prometheus_client.CollectorRegistry`
            to use. Defaults to the module's default registry. Pass
            a custom one to isolate tests.
        reset: If ``True``, the default registry is *cleared* on
            construction. Useful in tests that need a clean slate;
            production code should leave it ``False`` so multiple
            components in the same process accumulate into the
            same scrape.

    Thread-safety: :mod:`prometheus_client` is thread-safe for
    ``inc`` / ``observe`` / ``set``; the registry is global state
    so constructing multiple :class:`PrometheusMetrics` with
    ``reset=False`` in the same process is supported (they share
    the registry).
    """

    def __init__(
        self,
        *,
        namespace: str = "hh_apply",
        registry: Any | None = None,
        reset: bool = False,
    ) -> None:
        pc = _require_prometheus()
        self._pc = pc
        self._namespace = namespace
        if registry is None and reset:
            # Clear the default registry so tests start from a known
            # state. Production code keeps the default registry.
            self._registry = pc.CollectorRegistry()
        elif registry is None:
            self._registry = pc.REGISTRY
        else:
            self._registry = registry

    def counter(
        self,
        name: str,
        help: str,
        *,
        labels: tuple[str, ...] = (),
    ) -> Counter:
        """Register / return a counter.

        The ``_total`` suffix is appended automatically if absent
        -- that's the Prometheus convention. Re-registering a
        name with the same labels returns the existing handle.
        """
        full_name = self._qualify(name)
        if not full_name.endswith("_total"):
            full_name = f"{full_name}_total"
        return cast(
            "Counter",
            self._pc.Counter(
                full_name,
                help,
                labelnames=labels or (),
                registry=self._registry,
            ),
        )

    def histogram(
        self,
        name: str,
        help: str,
        *,
        labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
    ) -> Histogram:
        """Register / return a histogram with the given bucket set.

        The default bucket set is the Prometheus standard
        ``(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10)`` --
        tuned for HTTP latencies in the 5ms-10s range, which
        covers the HH API's typical 100-1000 ms responses plus
        the long tail.
        """
        full_name = self._qualify(name)
        return cast(
            "Histogram",
            self._pc.Histogram(
                full_name,
                help,
                labelnames=labels or (),
                buckets=buckets,
                registry=self._registry,
            ),
        )

    def gauge(
        self,
        name: str,
        help: str,
        *,
        labels: tuple[str, ...] = (),
    ) -> Gauge:
        """Register / return a gauge."""
        full_name = self._qualify(name)
        return cast(
            "Gauge",
            self._pc.Gauge(
                full_name,
                help,
                labelnames=labels or (),
                registry=self._registry,
            ),
        )

    # ─── internal helpers ─────────────────────────────────────

    def _qualify(self, name: str) -> str:
        """Prefix ``name`` with the namespace (idempotently).

        A caller can pass either ``"hh_apply_foo"`` (already
        qualified) or ``"foo"`` (short form) -- both produce the
        same final name. The check is "starts with the namespace
        followed by an underscore".
        """
        prefix = f"{self._namespace}_"
        if not name.startswith(prefix):
            return f"{prefix}{name}"
        return name


# ─── Module-level helpers ───────────────────────────────────────


def init_metrics(
    *,
    namespace: str = "hh_apply",
    reset: bool = False,
) -> PrometheusMetrics:
    """Construct a fresh :class:`PrometheusMetrics` for the daemon.

    A thin factory so the call site in :mod:`job_bot.cli.observability`
    can import one symbol instead of two. The function lives here
    (next to the implementation) rather than in the CLI module
    so the tests can call it without pulling the CLI's argparse
    setup.

    Args:
        namespace: The metric-name prefix.
        reset: Pass ``True`` in test entry points to start from a
            clean registry. Production callers leave it ``False``
            so the daemon can mount alongside other components.

    Returns:
        A ready-to-use :class:`PrometheusMetrics` instance.
    """
    return PrometheusMetrics(namespace=namespace, reset=reset)


def render_metrics() -> bytes:
    """Render the Prometheus text-format payload for the default registry.

    The :class:`MetricsServer` calls this on every ``GET /metrics``
    request. It's a thin wrapper around
    :func:`prometheus_client.generate_latest` so the bytes shape
    is identical to what a standalone ``prometheus_client``
    process would emit (Prometheus's text format spec is stable).

    Returns:
        UTF-8 encoded text. Newline-separated ``# HELP`` /
        ``# TYPE`` / value lines. Counter samples have a
        ``_total`` suffix per the convention.

    Raises:
        ImportError: if :mod:`prometheus_client` isn't installed
            (propagated from :func:`_require_prometheus`).
    """
    pc = _require_prometheus()
    return cast("bytes", pc.generate_latest(pc.REGISTRY))


__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "Metrics",
    "NullCounter",
    "NullGauge",
    "NullHistogram",
    "NullMetrics",
    "PrometheusMetrics",
    "init_metrics",
    "render_metrics",
]
