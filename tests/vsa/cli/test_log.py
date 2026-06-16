"""Tests for the ``log`` VSA sub-command (issue #147).

The ``log`` op opens ``LOG_FILENAME`` (from the shared settings) in the
configured pager (``$PAGER``, defaults to ``less``) and supports a
``--follow`` flag for live tailing.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

import pytest

from job_bot.cli.log import Operation


class _FakeSettings:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path


class _FakeSlice:
    def __init__(self, settings: _FakeSettings) -> None:
        self.settings = settings


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser("log")
    Operation().setup_parser(sub)
    return parser


class TestLogSetupParser:
    def test_no_args(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["log"])
        assert ns.follow is False

    def test_follow_flag(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["log", "--follow"])
        assert ns.follow is True


class TestLogRun:
    def test_missing_log_file_returns_1(
        self,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        op = Operation(
            slice_=_FakeSlice(_FakeSettings(Path("/nonexistent/log.txt")))
        )

        parser = _make_parser()
        ns = parser.parse_args(["log"])
        rc = op.run(ns)

        assert rc == 1
        assert any(
            "не найден" in rec.message.lower()
            or "not found" in rec.message.lower()
            for rec in caplog.records
        )

    def test_invokes_pager(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        log_path = tmp_path / "log.txt"
        log_path.write_text("hello\n")
        monkeypatch.setenv("PAGER", "cat")

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], *, check: bool = False) -> Any:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        op = Operation(slice_=_FakeSlice(_FakeSettings(log_path)))
        parser = _make_parser()
        ns = parser.parse_args(["log"])
        rc = op.run(ns)

        assert rc == 0
        assert len(calls) == 1
        # Pager was invoked with the log path as the last arg.
        assert calls[0][0] == "cat"
        assert calls[0][-1] == str(log_path)

    def test_follow_adds_plus_F(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        log_path = tmp_path / "log.txt"
        log_path.write_text("hello\n")
        monkeypatch.setenv("PAGER", "less")

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], *, check: bool = False) -> Any:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        op = Operation(slice_=_FakeSlice(_FakeSettings(log_path)))
        parser = _make_parser()
        ns = parser.parse_args(["log", "--follow"])
        op.run(ns)

        # less's follow flag is appended as a positional argument.
        assert calls[0][0] == "less"
        assert "+F" in calls[0]

    def test_missing_pager_returns_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        log_path = tmp_path / "log.txt"
        log_path.write_text("hello\n")
        monkeypatch.setenv("PAGER", "no-such-pager")
        monkeypatch.setattr(
            "shutil.which",
            lambda name: None if name == "no-such-pager" else "/usr/bin/less",
        )

        op = Operation(slice_=_FakeSlice(_FakeSettings(log_path)))
        parser = _make_parser()
        ns = parser.parse_args(["log"])
        rc = op.run(ns)

        assert rc == 1
        assert any(
            "no-such-pager" in rec.message
            or "просмотрщик" in rec.message.lower()
            for rec in caplog.records
        )
