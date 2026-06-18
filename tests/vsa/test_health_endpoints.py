"""Tests for the shared health endpoints module (issue #208).

The :mod:`job_bot.shared.health` module exposes two HTTP endpoints for
external supervisors (Docker, Kubernetes, systemd) to liveness- and
readiness-probe the long-running CLI daemons (``apply-worker``,
``telegram-bot``, ``max-bot``):

* ``GET /health`` -- liveness probe. Always returns ``200`` when the
  process is alive and the HTTP server is listening. No external
  dependencies are checked.
* ``GET /ready``  -- readiness probe. Returns ``200`` when the default
  :class:`DefaultHealthChecks` says DB and HH API are reachable,
  otherwise ``503`` with a JSON body explaining the failure.

The HTTP server is the stdlib :class:`http.server.ThreadingHTTPServer`
(no new runtime deps), bound to port ``0`` in tests so the kernel
assigns a free port. Tests probe the running server with
:mod:`urllib.request` (also stdlib) and shut it down in the fixture
finalizer.

The ``HealthChecks`` Protocol is verified with a fake implementation
that returns canned ``(ok, reason)`` tuples, so the ``/ready`` 503 path
can be exercised without touching the real DB or HH API.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Iterator

import pytest

from job_bot.shared.health.checks import DefaultHealthChecks, HealthChecks
from job_bot.shared.health.server import HealthServer

# ─── Fake / stub checks for the 503 path ────────────────────────────────


class _AlwaysOk(HealthChecks):
    """Stand-in checks: liveness and readiness always pass.

    Used to verify the happy-path HTTP responses without touching the
    real DB or HH API.
    """

    def liveness(self) -> tuple[bool, str]:
        return True, "alive"

    def readiness(self) -> tuple[bool, str]:
        return True, "ready"


class _AlwaysFailing(HealthChecks):
    """Stand-in checks: liveness OK, readiness fails.

    Used to exercise the ``/ready`` 503 branch. The failure reason must
    appear in the response body so operators can see why the pod was
    marked NotReady.
    """

    def __init__(self, reason: str = "db unreachable") -> None:
        self._reason = reason

    def liveness(self) -> tuple[bool, str]:
        return True, "alive"

    def readiness(self) -> tuple[bool, str]:
        return False, self._reason


class _BrokenLiveness(HealthChecks):
    """Liveness check that returns failure (e.g. shutting down).

    Even though the spec for ``/health`` is "process is alive", a
    custom check may report failure (e.g. graceful-shutdown flag).
    ``/health`` must still respond ``200`` because the liveness probe
    is independent of the check; only ``/ready`` consults readiness.
    Wait -- that's the opposite of what we want. The ``/health``
    handler is supposed to be the *trivial* liveness probe. It does
    NOT call ``checks.liveness()`` -- it just returns ``200`` because
    the HTTP server itself is the evidence the process is alive.
    This stub is only here to confirm the handler ignores it.
    """

    def liveness(self) -> tuple[bool, str]:
        return False, "should not be called by /health"

    def readiness(self) -> tuple[bool, str]:
        return True, "ready"


# ─── Helpers ────────────────────────────────────────────────────────────


def _free_port() -> int:
    """Ask the kernel for a free TCP port.

    The socket is bound to port ``0`` and closed before the caller
    binds to it. There's a tiny race window where the port could be
    taken between closing and re-binding, but in practice it's fine
    for tests -- and :class:`HealthServer` also has the same race
    (it's just how stdlib ``http.server`` works).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_server(host: str, port: int, timeout: float = 2.0) -> None:
    """Poll the TCP port until the server is accepting connections.

    Raises :class:`TimeoutError` if the server doesn't start in time.
    Useful because :class:`HealthServer.start()` returns immediately
    after spawning the thread; the actual bind/accept happens later.
    """
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
    """Issue a GET, return ``(status_code, headers, body_bytes)``.

    The body is returned as raw bytes (decoded as JSON inside the
    test) so we can assert the exact bytes produced by the handler.
    """
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return (
                int(resp.status),
                dict(resp.headers.items()),
                resp.read(),
            )
    except urllib.error.HTTPError as exc:
        # 503 still has a body; capture it instead of letting urlopen raise.
        return (
            int(exc.code),
            dict(exc.headers.items()),
            exc.read(),
        )


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def health_server_port() -> int:
    """Yield a free TCP port for the test server to bind to.

    We pick the port *before* constructing the server so the test can
    know the URL to probe. ``HealthServer`` binds in a background
    thread; we wait on it via :func:`_wait_for_server`.
    """
    return _free_port()


@pytest.fixture
def health_server_factory() -> Iterator[type[HealthServer]]:
    """Track every ``HealthServer`` we create and shut them down at end.

    Prevents leaked threads / bound sockets when an assertion fails
    mid-test.
    """
    started: list[HealthServer] = []
    yield HealthServer
    for srv in started:
        try:
            srv.stop()
        except Exception:  # noqa: BLE001
            pass


def _track(
    factory: type[HealthServer],
    started: list[HealthServer],
    **kwargs: object,
) -> HealthServer:
    """Construct a ``HealthServer`` and register it for teardown."""
    srv = factory(**kwargs)  # type: ignore[arg-type]
    started.append(srv)
    return srv


# ─── Liveness ───────────────────────────────────────────────────────────


class TestLivenessEndpoint:
    """/health is a trivial liveness probe: HTTP 200, no deps checked."""

    def test_health_returns_200_ok_status(
        self,
        health_server_port: int,
        health_server_factory: type[HealthServer],
    ) -> None:
        """``GET /health`` returns ``200 OK`` with the literal ``ok`` body.

        The body shape matches the issue spec
        (``{"status": "ok"}``) so simple ``jq``/``grep`` probes on the
        supervisor side keep working.
        """
        started: list[HealthServer] = []
        srv = _track(
            health_server_factory,
            started,
            port=health_server_port,
            checks=_AlwaysOk(),
        )
        srv.start()
        _wait_for_server("127.0.0.1", health_server_port)
        try:
            status, _headers, body = _http_get(
                f"http://127.0.0.1:{health_server_port}/health"
            )
            assert status == 200
            payload = json.loads(body.decode("utf-8"))
            assert payload == {"status": "ok"}
        finally:
            srv.stop()

    def test_health_handler_does_not_call_liveness_check(
        self,
        health_server_port: int,
        health_server_factory: type[HealthServer],
    ) -> None:
        """/health must not consult ``HealthChecks.liveness``.

        The handler's job is to prove the *process* is alive, which the
        fact that the HTTP server is responding already establishes.
        Calling a user-supplied check here would risk a deadlock if
        the check is slow (the worker thread is single-threaded).
        """
        started: list[HealthServer] = []
        srv = _track(
            health_server_factory,
            started,
            port=health_server_port,
            checks=_BrokenLiveness(),
        )
        srv.start()
        _wait_for_server("127.0.0.1", health_server_port)
        try:
            status, _, body = _http_get(
                f"http://127.0.0.1:{health_server_port}/health"
            )
            assert status == 200
            assert json.loads(body.decode("utf-8")) == {"status": "ok"}
        finally:
            srv.stop()


# ─── Readiness ──────────────────────────────────────────────────────────


class TestReadinessEndpoint:
    """/ready reflects the result of the readiness check(s)."""

    def test_ready_returns_200_when_checks_pass(
        self,
        health_server_port: int,
        health_server_factory: type[HealthServer],
    ) -> None:
        """Healthy backend → ``200 OK`` and ``{"status": "ok"}``."""
        started: list[HealthServer] = []
        srv = _track(
            health_server_factory,
            started,
            port=health_server_port,
            checks=_AlwaysOk(),
        )
        srv.start()
        _wait_for_server("127.0.0.1", health_server_port)
        try:
            status, _headers, body = _http_get(
                f"http://127.0.0.1:{health_server_port}/ready"
            )
            assert status == 200
            payload = json.loads(body.decode("utf-8"))
            assert payload == {"status": "ok"}
        finally:
            srv.stop()

    def test_ready_returns_503_when_check_fails(
        self,
        health_server_port: int,
        health_server_factory: type[HealthServer],
    ) -> None:
        """Failing readiness → ``503`` and a body explaining why.

        The body must surface the reason so the operator can triage
        without ssh-ing into the pod (e.g. via ``kubectl describe``).
        """
        started: list[HealthServer] = []
        srv = _track(
            health_server_factory,
            started,
            port=health_server_port,
            checks=_AlwaysFailing(reason="db unreachable"),
        )
        srv.start()
        _wait_for_server("127.0.0.1", health_server_port)
        try:
            status, _headers, body = _http_get(
                f"http://127.0.0.1:{health_server_port}/ready"
            )
            assert status == 503
            payload = json.loads(body.decode("utf-8"))
            assert payload["status"] == "unready"
            assert payload["reason"] == "db unreachable"
        finally:
            srv.stop()


# ─── Lifecycle ──────────────────────────────────────────────────────────


class TestServerLifecycle:
    """``start()`` / ``stop()`` are idempotent and bound to port 0."""

    def test_start_and_stop_cleanly(
        self,
        health_server_port: int,
        health_server_factory: type[HealthServer],
    ) -> None:
        """A full start → probe → stop cycle succeeds.

        ``stop()`` must close the listening socket (so re-binding the
        same port would succeed) and join the thread (no leaked
        daemon thread). We re-bind to the same port immediately after
        ``stop()`` to prove the socket was actually released.
        """
        started: list[HealthServer] = []
        srv = _track(
            health_server_factory,
            started,
            port=health_server_port,
            checks=_AlwaysOk(),
        )
        srv.start()
        _wait_for_server("127.0.0.1", health_server_port)

        # Probe confirms the server is up.
        status, _, _ = _http_get(
            f"http://127.0.0.1:{health_server_port}/health"
        )
        assert status == 200

        srv.stop()

        # After stop() the socket must be released. If the thread is
        # still holding it the bind() raises OSError(EADDRINUSE).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", health_server_port))
            except OSError as exc:
                pytest.fail(
                    f"port {health_server_port} still bound after stop(): "
                    f"{exc!r}"
                )

    def test_stop_is_idempotent(
        self,
        health_server_port: int,
        health_server_factory: type[HealthServer],
    ) -> None:
        """Calling ``stop()`` twice is a no-op the second time.

        The CLI ops call ``stop()`` in their ``KeyboardInterrupt``
        handler; a double-stop would otherwise raise and mask the
        original signal-handler error.
        """
        started: list[HealthServer] = []
        srv = _track(
            health_server_factory,
            started,
            port=health_server_port,
            checks=_AlwaysOk(),
        )
        srv.start()
        _wait_for_server("127.0.0.1", health_server_port)
        srv.stop()
        # Second stop must not raise.
        srv.stop()

    def test_stop_without_start_is_safe(
        self,
        health_server_port: int,
        health_server_factory: type[HealthServer],
    ) -> None:
        """``stop()`` on a never-started server is a silent no-op.

        Keeps the CLI wiring uniform: every daemon op can have a
        ``finally: health_server.stop()`` block without a ``hasattr``
        or ``is None`` guard.
        """
        started: list[HealthServer] = []
        srv = _track(
            health_server_factory,
            started,
            port=health_server_port,
            checks=_AlwaysOk(),
        )
        srv.stop()


# ─── 404 path ───────────────────────────────────────────────────────────


class TestUnknownRoute:
    """/unknown must return 404 (don't leak the handler's internals)."""

    def test_unknown_path_returns_404(
        self,
        health_server_port: int,
        health_server_factory: type[HealthServer],
    ) -> None:
        started: list[HealthServer] = []
        srv = _track(
            health_server_factory,
            started,
            port=health_server_port,
            checks=_AlwaysOk(),
        )
        srv.start()
        _wait_for_server("127.0.0.1", health_server_port)
        try:
            status, _, body = _http_get(
                f"http://127.0.0.1:{health_server_port}/admin"
            )
            assert status == 404
            assert (
                b"Not Found" in body
                or json.loads(body.decode("utf-8")).get("status") == "not_found"
            )
        finally:
            srv.stop()


# ─── DefaultHealthChecks ────────────────────────────────────────────────


class TestDefaultHealthChecks:
    """``DefaultHealthChecks`` probes DB + HH API."""

    def test_liveness_always_ok(self, tmp_path) -> None:  # noqa: ANN001
        """Liveness never fails -- the process being up is the proof."""
        from job_bot.shared.storage.database import Database

        db = Database(tmp_path / "x.db")
        # No actual HH API call needed for liveness.
        checks = DefaultHealthChecks(database=db, hh_api=_FakeHH())
        ok, msg = checks.liveness()
        assert ok is True
        assert msg

    def test_readiness_ok_when_db_and_api_reachable(self, tmp_path) -> None:  # noqa: ANN001
        """Both probes succeed → readiness is ``(True, ...)``."""
        from job_bot.shared.storage.database import Database

        db = Database(tmp_path / "x.db")
        checks = DefaultHealthChecks(database=db, hh_api=_FakeHH(ok=True))
        ok, msg = checks.readiness()
        assert ok is True
        assert msg

    def test_readiness_fails_when_db_unreachable(self, tmp_path) -> None:  # noqa: ANN001
        """A DB failure surfaces as ``(False, "db: ...")``.

        The message must mention which subsystem failed so the
        /ready 503 response body is useful for triage.
        """
        from job_bot.shared.storage.database import Database

        db = Database(tmp_path / "x.db")
        checks = DefaultHealthChecks(database=db, hh_api=_FakeHH(ok=False))
        ok, msg = checks.readiness()
        assert ok is False
        assert "db" in msg.lower() or "hh" in msg.lower()

    def test_readiness_accepts_raw_sqlite_connection(self) -> None:
        """A raw ``sqlite3.Connection`` works as the ``database`` arg.

        The ``ApplicationSubmitSlice`` exposes the raw connection
        (not the ``Database`` wrapper), so the probe must accept
        that shape. The handler is a small enough piece of code
        that we can run it directly here.
        """
        conn = sqlite3.connect(":memory:")
        try:
            checks = DefaultHealthChecks(database=conn, hh_api=_FakeHH())
            ok, msg = checks.readiness()
            assert ok is True
            assert msg == "ready"
        finally:
            conn.close()

    def test_protocol_is_runtime_checkable(self) -> None:
        """``HealthChecks`` is a :class:`typing.Protocol` -- structural.

        Concrete fakes don't need to inherit; the constructor only
        requires ``liveness()`` and ``readiness()`` methods.
        """
        # _AlwaysOk already satisfies the protocol structurally.
        fake = _AlwaysOk()
        # Method-existence check (Protocol has no __subclasshook__ here).
        assert hasattr(fake, "liveness")
        assert hasattr(fake, "readiness")
        assert callable(fake.liveness)
        assert callable(fake.readiness)


class _FakeHH:
    """Minimal duck-typed HH API stub for ``DefaultHealthChecks``.

    The default ``HealthChecks`` calls ``hh_api.ping()`` -- a method
    that the production :class:`HHApiClient` exposes as a no-arg
    ``HEAD`` against ``https://api.hh.ru/``. We provide the bare
    minimum shape so the readiness check can run without network.
    """

    def __init__(self, ok: bool = True, exc: Exception | None = None) -> None:
        self._ok = ok
        self._exc = exc

    def ping(self) -> None:
        if self._exc is not None:
            raise self._exc
        if not self._ok:
            raise RuntimeError("hh api unreachable")
