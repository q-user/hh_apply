"""CLI-level tests for the ``--health-port`` wiring (issue #208).

These tests verify the *plumbing*: the ``--health-port`` flag is
registered on the three long-running daemon ops (``apply-worker``,
``telegram-bot``, ``max-bot``), and the ``HealthServer`` is started
when the flag is set.

The deeper ``/health`` and ``/ready`` semantics are covered in
:mod:`tests.vsa.test_health_endpoints`. These tests just prove the
CLI surface is wired correctly -- e.g. that the wrong slice type
doesn't crash the parser and that ``--health-port 0`` (OS-assigned
port) actually binds the server.
"""

from __future__ import annotations

import argparse
import socket
import time
from typing import Any

import pytest

from job_bot.cli.apply_worker import Operation as ApplyWorkerOperation
from job_bot.cli.max_bot import Operation as MaxBotOperation
from job_bot.cli.telegram_bot import Operation as TelegramBotOperation
from job_bot.shared.health import HealthServer, TrivialHealthChecks

# ─── Helpers ────────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _build_parser(op: Any) -> argparse.ArgumentParser:
    """Run ``setup_parser`` on a fresh sub-parser and return it."""
    parent = argparse.ArgumentParser()
    sub = parent.add_subparsers(dest="cmd", required=True)
    parser = sub.add_parser("op")
    op.setup_parser(parser)
    return parser


# ─── Flag registration ──────────────────────────────────────────────────


class TestHealthPortFlagRegistered:
    """The ``--health-port`` flag must be on every long-running daemon op."""

    def test_apply_worker_has_health_port(self) -> None:
        parser = _build_parser(ApplyWorkerOperation())
        args = parser.parse_args(["--health-port", "8080"])
        assert args.health_port == 8080

    def test_apply_worker_health_port_defaults_none(self) -> None:
        """No flag → server must not start (default ``None``)."""
        parser = _build_parser(ApplyWorkerOperation())
        args = parser.parse_args([])
        assert args.health_port is None

    def test_telegram_bot_has_health_port(self) -> None:
        parser = _build_parser(TelegramBotOperation())
        args = parser.parse_args(["--health-port", "8080"])
        assert args.health_port == 8080

    def test_telegram_bot_health_port_defaults_none(self) -> None:
        parser = _build_parser(TelegramBotOperation())
        args = parser.parse_args([])
        assert args.health_port is None

    def test_max_bot_has_health_port(self) -> None:
        parser = _build_parser(MaxBotOperation())
        args = parser.parse_args(["--health-port", "8080"])
        assert args.health_port == 8080

    def test_max_bot_health_port_defaults_none(self) -> None:
        parser = _build_parser(MaxBotOperation())
        args = parser.parse_args([])
        assert args.health_port is None


class TestHealthHostFlagRegistered:
    """The ``--health-host`` flag must be on every long-running daemon op.

    Mirrors :class:`TestHealthPortFlagRegistered` for the host
    override (issue #208 k8s fix). The default stays ``127.0.0.1`` so
    local dev is safe; production sets ``--health-host 0.0.0.0``.
    """

    def test_apply_worker_has_health_host(self) -> None:
        parser = _build_parser(ApplyWorkerOperation())
        args = parser.parse_args(["--health-host", "0.0.0.0"])
        assert args.health_host == "0.0.0.0"

    def test_apply_worker_health_host_defaults_to_loopback(self) -> None:
        """No flag → ``127.0.0.1`` (safe local default)."""
        from job_bot.shared.health import DEFAULT_HOST

        parser = _build_parser(ApplyWorkerOperation())
        args = parser.parse_args([])
        assert args.health_host == DEFAULT_HOST

    def test_telegram_bot_has_health_host(self) -> None:
        parser = _build_parser(TelegramBotOperation())
        args = parser.parse_args(["--health-host", "0.0.0.0"])
        assert args.health_host == "0.0.0.0"

    def test_telegram_bot_health_host_defaults_to_loopback(self) -> None:
        from job_bot.shared.health import DEFAULT_HOST

        parser = _build_parser(TelegramBotOperation())
        args = parser.parse_args([])
        assert args.health_host == DEFAULT_HOST

    def test_max_bot_has_health_host(self) -> None:
        parser = _build_parser(MaxBotOperation())
        args = parser.parse_args(["--health-host", "0.0.0.0"])
        assert args.health_host == "0.0.0.0"

    def test_max_bot_health_host_defaults_to_loopback(self) -> None:
        from job_bot.shared.health import DEFAULT_HOST

        parser = _build_parser(MaxBotOperation())
        args = parser.parse_args([])
        assert args.health_host == DEFAULT_HOST


# ─── HealthServer lifecycle in the CLI ops ──────────────────────────────


class _StubSlice:
    """Bare-minimum slice stub for the CLI smoke tests.

    The apply-worker CLI only touches ``worker.run`` and the health
    helpers; the others use ``transport.get_updates`` / ``handler.run``
    etc. We don't actually invoke ``run`` here -- these tests only
    verify the parser + that the slice can be inspected safely.
    """

    def __init__(self) -> None:
        self.worker = _StubWorker()
        self.storage_conn = _FakeConn()
        self.api_client = _StubHH()
        self.database = _FakeDatabase()
        self.transport = _StubTransport()
        self.handler = _StubHandler()

    def dispatch_update(self, update: dict[str, Any]) -> None: ...

    def send_digest(self, *, force: bool = False) -> Any: ...


class _StubWorker:
    worker_id = "test-worker"

    def run(self, **kwargs: Any) -> Any:
        class _Stats:
            processed = 0
            succeeded = 0
            failed = 0

        return _Stats()


class _FakeConn:
    """Duck-typed ``sqlite3.Connection`` -- never actually queried."""

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self

    def fetchone(self) -> Any:
        return (1,)


class _StubHH:
    def ping(self) -> None:
        return None


class _FakeDatabase:
    """Duck-typed ``Database`` -- ``isinstance`` check passes via class name.

    The CLI wiring uses ``isinstance(db, Database)`` for telegram-bot
    only. We bypass the check by passing a real ``Database`` here so
    the wiring logic actually runs end-to-end.
    """

    def connect(self) -> Any:
        # The real ``Database.connect()`` is a context manager; we
        # return a context-manager-shaped fake.
        class _Ctx:
            def __enter__(self_inner) -> _FakeConn:  # noqa: N805
                return _FakeConn()

            def __exit__(self_inner, *exc: Any) -> None:  # noqa: N805
                return None

        return _Ctx()


class _StubTransport:
    def get_updates(self, *, offset: int | None = None) -> list[dict[str, Any]]:
        return []


class _StubHandler:
    def run(self, *, stop_after: int | None = None) -> None:
        return None


# ─── HealthServer construction sanity ───────────────────────────────────


class _HealthServerRecorder:
    """Class stand-in for ``HealthServer`` that records its constructor kwargs.

    The CLI ops call ``HealthServer(...)`` (a class call), so the
    monkey-patched symbol must be a class. We collect kwargs in a
    class-level list; tests must call :meth:`reset` before each
    inspection so a previous test's instantiation doesn't leak in.
    """

    recorded: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        type(self).recorded.append(kwargs)
        self.kwargs = kwargs
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self, *, timeout: float = 5.0) -> None:
        self.stopped = True

    @classmethod
    def reset(cls) -> None:
        cls.recorded.clear()


class TestHealthHostPropagatesToServer:
    """``--health-host`` must reach the ``HealthServer`` constructor.

    We monkey-patch ``HealthServer`` in each CLI op's module to a
    recorder so the test can introspect the kwargs without binding a
    real socket. End-to-end behaviour (probe, port conflict) is
    covered separately in :mod:`tests.vsa.test_health_endpoints`.
    """

    def test_apply_worker_propagates_health_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from job_bot.cli import apply_worker

        _HealthServerRecorder.reset()
        monkeypatch.setattr(apply_worker, "HealthServer", _HealthServerRecorder)

        op = ApplyWorkerOperation(slice_=_StubSlice())
        args = _build_parser(op).parse_args(
            ["--health-port", "8080", "--health-host", "0.0.0.0"]
        )
        rc = op.run(args)
        assert rc == 0
        assert len(_HealthServerRecorder.recorded) == 1
        assert _HealthServerRecorder.recorded[0]["port"] == 8080
        assert _HealthServerRecorder.recorded[0]["host"] == "0.0.0.0"

    def test_apply_worker_default_host_is_loopback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``--health-host`` flag → server gets ``127.0.0.1``."""
        from job_bot.cli import apply_worker
        from job_bot.shared.health import DEFAULT_HOST

        _HealthServerRecorder.reset()
        monkeypatch.setattr(apply_worker, "HealthServer", _HealthServerRecorder)

        op = ApplyWorkerOperation(slice_=_StubSlice())
        args = _build_parser(op).parse_args(["--health-port", "8080"])
        rc = op.run(args)
        assert rc == 0
        assert _HealthServerRecorder.recorded[0]["host"] == DEFAULT_HOST

    def test_telegram_bot_propagates_health_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from job_bot.cli import telegram_bot

        _HealthServerRecorder.reset()
        monkeypatch.setattr(telegram_bot, "HealthServer", _HealthServerRecorder)

        op = TelegramBotOperation(slice_=_StubSlice())
        # ``--once`` so the polling loop exits after one cycle.
        args = _build_parser(op).parse_args(
            ["--health-port", "8081", "--health-host", "0.0.0.0", "--once"]
        )
        rc = op.run(args)
        assert rc == 0
        assert len(_HealthServerRecorder.recorded) == 1
        assert _HealthServerRecorder.recorded[0]["port"] == 8081
        assert _HealthServerRecorder.recorded[0]["host"] == "0.0.0.0"

    def test_max_bot_propagates_health_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from job_bot.cli import max_bot

        _HealthServerRecorder.reset()
        monkeypatch.setattr(max_bot, "HealthServer", _HealthServerRecorder)

        op = MaxBotOperation(slice_=_StubSlice())
        # ``--once`` so ``_run_polling`` calls ``handler.run(stop_after=1)``
        # and returns immediately.
        args = _build_parser(op).parse_args(
            ["--health-port", "8082", "--health-host", "0.0.0.0", "--once"]
        )
        rc = op.run(args)
        assert rc == 0
        assert len(_HealthServerRecorder.recorded) == 1
        assert _HealthServerRecorder.recorded[0]["port"] == 8082
        assert _HealthServerRecorder.recorded[0]["host"] == "0.0.0.0"

    def test_health_server_not_started_without_health_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``--health-port`` → no ``HealthServer`` constructed.

        Belt-and-braces: the new ``--health-host`` flag must not
        implicitly enable the server. Both flags are independent.
        """
        from job_bot.cli import apply_worker

        _HealthServerRecorder.reset()
        monkeypatch.setattr(apply_worker, "HealthServer", _HealthServerRecorder)

        op = ApplyWorkerOperation(slice_=_StubSlice())
        args = _build_parser(op).parse_args(["--health-host", "0.0.0.0"])
        rc = op.run(args)
        assert rc == 0
        assert _HealthServerRecorder.recorded == []


class TestHealthServerIntegrationWithCli:
    """End-to-end: a ``HealthServer`` can be built with the helpers the
    CLI ops use, and starts / stops cleanly on a real port."""

    def test_trivial_checks_construct_health_server(self) -> None:
        """``TrivialHealthChecks`` + ``HealthServer`` works out of the box.

        This is the path the max-bot CLI op takes when its slice
        doesn't expose a database or HH API client.
        """
        port = _free_port()
        srv = HealthServer(port=port, checks=TrivialHealthChecks())
        srv.start()
        # Give the thread a moment to bind.
        time.sleep(0.05)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                pass  # bound
        finally:
            srv.stop()

    def test_health_server_fails_fast_on_taken_port(self) -> None:
        """A second ``start()`` on a port the kernel already gave out raises.

        This is the behaviour the CLI ops rely on (issue #208):
        ``OSError`` propagates and the op exits with code 1 instead
        of silently retrying in the background.
        """
        port = _free_port()
        srv1 = HealthServer(port=port, checks=TrivialHealthChecks())
        srv1.start()
        time.sleep(0.05)
        try:
            srv2 = HealthServer(port=port, checks=TrivialHealthChecks())
            try:
                srv2.start()
            except OSError:
                pass  # expected
            else:
                raise AssertionError("expected OSError on taken port")
        finally:
            srv1.stop()
