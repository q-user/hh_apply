"""HTTP server for ``/health`` and ``/ready`` endpoints (issue #208).

A minimal, dependency-free health server built on the stdlib
:mod:`http.server`. The CLI daemons (``apply-worker``, ``telegram-bot``,
``max-bot``) start a :class:`HealthServer` in a background thread when
the ``--health-port`` flag is set, so an external supervisor (Docker,
Kubernetes, systemd) can liveness- and readiness-probe them.

Design notes:

* **Stdlib only.** ``aiohttp`` / ``starlette`` were the alternative
  picks (the issue mentions both) but the whole surface is two GETs;
  ``http.server.ThreadingHTTPServer`` is enough and avoids adding a
  runtime dep.
* **Thread-per-request.** The :class:`ThreadingHTTPServer` accepts
  connections on one thread and dispatches each request to a fresh
  worker thread, so a slow ``/ready`` probe never blocks the listening
  socket. Tests run with one daemon thread; production usage is the
  same.
* **Daemon thread.** The server thread is marked ``daemon=True`` so a
  hard process exit (no graceful ``stop()``) does not hang on the
  listener. ``stop()`` joins it cleanly when called.
* **Handler is stateless.** The :class:`_HealthHandler` closure binds
  the user-supplied :class:`HealthChecks` once at construction time;
  no global state, so concurrent requests are safe.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from job_bot.shared.health.checks import HealthChecks

logger = logging.getLogger(__package__)

DEFAULT_HOST = "127.0.0.1"


class HealthServer:
    """Tiny stdlib HTTP server exposing ``/health`` and ``/ready``.

    Args:
        port: TCP port to listen on. Use ``0`` for an OS-assigned
            port (tests do this; production passes the
            ``--health-port`` flag verbatim).
        checks: A :class:`HealthChecks` implementation whose
            ``readiness()`` is consulted by ``/ready``. ``/health``
            ignores ``checks.liveness()`` by design -- see the module
            docstring for the rationale.
        host: Interface to bind. Defaults to ``127.0.0.1`` so a
            developer-mode probe does not accidentally expose the
            server on a public interface. Production deployments
            override with ``0.0.0.0`` so the orchestrator can reach
            the pod.

    Thread-safety: ``start()`` / ``stop()`` must be called from a
    single thread (typically the main thread). The request-handling
    threads are spawned by :class:`ThreadingHTTPServer` and are safe
    to run concurrently.
    """

    def __init__(
        self,
        *,
        port: int,
        checks: HealthChecks,
        host: str = DEFAULT_HOST,
    ) -> None:
        self._port = int(port)
        self._host = host
        self._checks = checks
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ─── public API ────────────────────────────────────────────────

    def start(self) -> None:
        """Bind the listener and spawn the background thread.

        Returns as soon as the thread is launched; callers that need
        to wait for the port to be open (e.g. before issuing a
        probe) should poll ``socket.create_connection``.
        """
        if self._server is not None:
            logger.debug("health server already started; ignoring start()")
            return

        handler_cls = self._make_handler()
        # ThreadingHTTPServer binds immediately; if the port is taken
        # we let OSError bubble up so the CLI op can fail fast
        # instead of silently retrying in the background.
        self._server = ThreadingHTTPServer(
            (self._host, self._port), handler_cls
        )
        self._thread = threading.Thread(
            target=self._serve_forever,
            name="health-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("health server listening on %s:%d", self._host, self._port)

    def stop(self, *, timeout: float = 5.0) -> None:
        """Shut down the listener and join the thread.

        Idempotent: a second call on a stopped (or never-started)
        server is a silent no-op. ``timeout`` bounds the join so a
        wedged handler cannot hang the caller.
        """
        server = self._server
        if server is None:
            return
        # ``shutdown`` is idempotent in modern stdlib; any rare race is
        # swallowed by the broad ``except`` below so the caller can
        # still proceed to ``server_close()`` and join the thread.
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("health server shutdown() raised")
        try:
            server.server_close()
        except Exception:  # noqa: BLE001
            logger.exception("health server server_close() raised")

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._server = None
        self._thread = None
        logger.info("health server stopped")

    # ─── handler factory ──────────────────────────────────────────

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        """Build a ``BaseHTTPRequestHandler`` subclass bound to ``checks``.

        The closure captures ``self._checks`` so the handler stays
        a plain class (no per-request attribute lookup on the
        :class:`HealthServer`). ``log_error`` is silenced to keep
        the supervisor-side logs clean -- the real signal is the
        ``/ready`` 503 response body.
        """
        checks = self._checks

        class _HealthHandler(BaseHTTPRequestHandler):
            """Per-request handler -- constructed fresh for each connection."""

            # Silence the default per-request stderr log; the
            # server-side logger is already capturing the lifecycle
            # events (start / stop). 4xx / 5xx responses are still
            # visible via ``log_message`` (overridden below).
            def log_message(  # type: ignore[override]
                self, format: str, *args: Any
            ) -> None:  # noqa: A002 -- match stdlib signature
                logger.debug(format, *args)

            def do_GET(self) -> None:  # noqa: N802 -- stdlib name
                if self.path == "/health":
                    self._json(200, {"status": "ok"})
                elif self.path == "/ready":
                    self._ready()
                else:
                    self._json(404, {"status": "not_found"})

            # ─── handlers ─────────────────────────────────────

            def _ready(self) -> None:
                try:
                    ok, message = checks.readiness()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("/ready: check raised: %s", exc)
                    self._json(503, {"status": "unready", "reason": str(exc)})
                    return
                if ok:
                    self._json(200, {"status": "ok"})
                else:
                    self._json(503, {"status": "unready", "reason": message})

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                # Disable keep-alive: the supervisor opens a fresh
                # connection per probe, and turning keep-alive off
                # simplifies ``stop()`` (no half-open sockets).
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

        return _HealthHandler

    # ─── thread target ────────────────────────────────────────────

    def _serve_forever(self) -> None:
        """``ThreadingHTTPServer.serve_forever`` in the worker thread."""
        assert self._server is not None  # set by start()
        try:
            self._server.serve_forever()
        except Exception:  # noqa: BLE001
            logger.exception("health server crashed")
            raise


__all__ = ["DEFAULT_HOST", "HealthServer"]
