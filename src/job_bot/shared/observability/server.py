"""HTTP server for the ``/metrics`` Prometheus endpoint (issue #203).

A minimal, dependency-free HTTP server built on the stdlib
:mod:`http.server`. Mirrors the design of :class:`HealthServer` in
:mod:`job_bot.shared.health` -- one daemon thread, one GET route,
and zero runtime dependencies.

The :class:`MetricsServer` is intentionally separate from
:class:`HealthServer` so the two endpoints can be exposed on
different ports (or bound to different interfaces) without
co-evolving. The :class:`HealthChecks` Protocol + ``/ready`` route
is health-specific; mixing it with the Prometheus payload would
make the code harder to read and would force a single port.

Design notes
------------

* **Stdlib only.** ``aiohttp`` / ``starlette`` were alternatives
  but the whole surface is one GET; ``ThreadingHTTPServer`` is
  enough and avoids adding a runtime dep.
* **Thread-per-request.** :class:`ThreadingHTTPServer` accepts
  connections on one thread and dispatches each request to a
  fresh worker thread, so a slow Prometheus scrape never blocks
  the listening socket.
* **Daemon thread.** The server thread is marked ``daemon=True``
  so a hard process exit (no graceful ``stop()``) does not hang
  on the listener. ``stop()`` joins it cleanly when called.
* **Stateless handler.** The :class:`_MetricsHandler` closure
  binds nothing -- the Prometheus registry is process-global, so
  no per-request state is needed.
"""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logger = logging.getLogger(__package__)

DEFAULT_HOST = "127.0.0.1"


class MetricsServer:
    """Tiny stdlib HTTP server exposing ``GET /metrics``.

    Args:
        port: TCP port to listen on. Use ``0`` for an OS-assigned
            port (tests do this; production passes the
            ``--metrics-port`` flag verbatim).
        host: Interface to bind. Defaults to ``127.0.0.1`` so a
            developer-mode probe does not accidentally expose the
            server on a public interface. Production deployments
            override with ``0.0.0.0`` so the Prometheus scraper
            can reach the pod.

    Thread-safety: ``start()`` / ``stop()`` must be called from a
    single thread (typically the main thread). The request-handling
    threads are spawned by :class:`ThreadingHTTPServer` and are
    safe to run concurrently.

    The /metrics payload is rendered lazily on every request by
    calling :func:`render_metrics` from
    :mod:`job_bot.shared.observability.metrics`. If the optional
    ``prometheus-client`` extra isn't installed, the server still
    binds but every GET returns ``503`` with a clear install hint
    (the :mod:`prometheus_client` import is inside the handler so
    the server itself is importable in minimal environments).
    """

    def __init__(
        self,
        *,
        port: int,
        host: str = DEFAULT_HOST,
    ) -> None:
        self._port = int(port)
        self._host = host
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ─── public API ────────────────────────────────────────────────

    def start(self) -> None:
        """Bind the listener and spawn the background thread.

        Returns as soon as the thread is launched; callers that
        need to wait for the port to be open (e.g. before issuing
        a scrape) should poll :func:`socket.create_connection`.
        """
        if self._server is not None:
            logger.debug("metrics server already started; ignoring start()")
            return

        handler_cls = self._make_handler()
        # ThreadingHTTPServer binds immediately; if the port is
        # taken we let OSError bubble up so the CLI op can fail
        # fast instead of silently retrying in the background.
        self._server = ThreadingHTTPServer(
            (self._host, self._port), handler_cls
        )
        self._thread = threading.Thread(
            target=self._serve_forever,
            name="metrics-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("metrics server listening on %s:%d", self._host, self._port)

    def stop(self, *, timeout: float = 5.0) -> None:
        """Shut down the listener and join the thread.

        Idempotent: a second call on a stopped (or never-started)
        server is a silent no-op. ``timeout`` bounds the join so
        a wedged handler cannot hang the caller.
        """
        server = self._server
        if server is None:
            return
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("metrics server shutdown() raised")
        try:
            server.server_close()
        except Exception:  # noqa: BLE001
            logger.exception("metrics server server_close() raised")

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._server = None
        self._thread = None
        logger.info("metrics server stopped")

    # ─── handler factory ──────────────────────────────────────────

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        """Build a ``BaseHTTPRequestHandler`` subclass for ``/metrics``.

        The handler is stateless -- the Prometheus registry is
        process-global, so no per-request state is needed.
        ``log_error`` is silenced to keep the supervisor-side logs
        clean; the real signal is the response status code.
        """

        class _MetricsHandler(BaseHTTPRequestHandler):
            """Per-request handler -- constructed fresh for each connection."""

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 -- match stdlib signature
                logger.debug(format, *args)

            def do_GET(self) -> None:  # noqa: N802 -- stdlib name
                if self.path == "/metrics":
                    self._metrics()
                else:
                    # 404 for unknown paths -- same shape as
                    # :class:`HealthServer` so probes and scrapers
                    # can rely on the same "this route is unknown"
                    # signal.
                    self._text(404, "not found\n")

            # ─── handlers ─────────────────────────────────────

            def _metrics(self) -> None:
                # Import inside the handler so the server can be
                # constructed in environments without the
                # ``prometheus-client`` extra -- the failure is
                # surfaced as a 503 with an install hint, not a
                # hard ImportError at server-startup time.
                try:
                    from job_bot.shared.observability.metrics import (
                        render_metrics,
                    )
                except ImportError:  # pragma: no cover - depends on install
                    self._text(
                        503,
                        "prometheus_client is not installed; "
                        "install the 'observability' extra to enable "
                        "/metrics.\n",
                    )
                    return
                try:
                    payload = render_metrics()
                except ImportError as exc:
                    # The optional dep is not installed; same 503
                    # path as above so the scrape pipeline
                    # surfaces a clear error.
                    self._text(503, f"{exc}\n")
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.exception("metrics: render failed")
                    self._text(500, f"render failed: {exc}\n")
                    return
                self.send_response(200)
                # The Prometheus text format spec is ``text/plain;
                # version=0.0.4; charset=utf-8`` -- emit that
                # exactly so Prometheus's content-type matcher
                # accepts the response.
                self.send_header(
                    "Content-Type",
                    "text/plain; version=0.0.4; charset=utf-8",
                )
                self.send_header("Content-Length", str(len(payload)))
                # Disable keep-alive: the scraper opens a fresh
                # connection per scrape, and turning keep-alive
                # off simplifies ``stop()`` (no half-open
                # sockets).
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)

            def _text(self, status: int, body: str) -> None:
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(encoded)

        return _MetricsHandler

    # ─── thread target ────────────────────────────────────────────

    def _serve_forever(self) -> None:
        """``ThreadingHTTPServer.serve_forever`` in the worker thread."""
        assert self._server is not None  # set by start()
        try:
            self._server.serve_forever()
        except Exception:  # noqa: BLE001
            logger.exception("metrics server crashed")
            raise


__all__ = ["DEFAULT_HOST", "MetricsServer"]
