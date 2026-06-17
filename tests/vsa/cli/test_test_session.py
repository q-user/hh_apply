"""Tests for the ``test_session`` VSA sub-command (issue #147).

The ``test_session`` op is a thin VSA adapter that hits ``https://hh.ru``
with the configured session and prints the logged-in user (or warns if
not logged in).
"""

from __future__ import annotations

import argparse

import pytest

from job_bot.cli.test_session import Operation


class _FakeResponse:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeSession:
    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.response = response or _FakeResponse()
        self.calls: list[str] = []

    def get(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        return self.response


class _FakeSlice:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser("test-session")
    Operation().setup_parser(sub)
    return parser


class TestTestSessionSetupParser:
    def test_no_args(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["test-session"])
        assert ns.cmd == "test-session"


class TestTestSessionRun:
    def test_prints_login_when_logged_in(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body = 'var x = {foo: 1};\n    login: "ivan@example.com",\n  };\n'
        session = _FakeSession(_FakeResponse(body))
        op = Operation(slice_=_FakeSlice(session))

        parser = _make_parser()
        ns = parser.parse_args(["test-session"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert "ivan@example.com" in out
        assert "вошли" in out.lower() or "logged" in out.lower()

    def test_warns_when_not_logged_in(
        self,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        session = _FakeSession(_FakeResponse("<html>anon</html>"))
        op = Operation(slice_=_FakeSlice(session))

        parser = _make_parser()
        ns = parser.parse_args(["test-session"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        # No login name is printed.
        assert out.strip() == ""
        # A warning is logged instead.
        assert any(
            "не автор" in rec.message.lower()
            or "not logged" in rec.message.lower()
            for rec in caplog.records
        )
