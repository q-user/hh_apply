"""Tests for the ``check_proxy`` VSA sub-command (issue #147)."""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from job_bot.cli.check_proxy import Operation


class _FakeResponse:
    def __init__(self, text: str = "1.2.3.4") -> None:
        self.text = text


class _FakeSession:
    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.response = response or _FakeResponse()
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.proxies: dict[str, str] = {"http": "socks5://localhost:1080"}

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self.response


class _FakeSlice:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser("check-proxy")
    Operation().setup_parser(sub)
    return parser


class TestCheckProxySetupParser:
    def test_no_arguments(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["check-proxy"])
        assert ns.cmd == "check-proxy"


class TestCheckProxyRun:
    def test_runs_session_get_and_prints_ip(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        session = _FakeSession(_FakeResponse("9.9.9.9"))
        op = Operation(slice_=_FakeSlice(session))

        parser = _make_parser()
        ns = parser.parse_args(["check-proxy"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert out.strip() == "9.9.9.9"
        assert session.calls and session.calls[0][0].startswith("https://")

    def test_uses_configured_session(self) -> None:
        """The op must call the injected session, not construct a new one."""
        session = _FakeSession()
        op = Operation(slice_=_FakeSlice(session))

        parser = _make_parser()
        ns = parser.parse_args(["check-proxy"])
        op.run(ns)

        # Exactly one GET call was made on the injected session.
        assert len(session.calls) == 1
