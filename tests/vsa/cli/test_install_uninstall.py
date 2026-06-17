"""Tests for the ``install`` and ``uninstall`` VSA sub-commands (issue #147)."""

from __future__ import annotations

import argparse

import pytest

from job_bot.cli.install import Operation as InstallOp
from job_bot.cli.uninstall import Operation as UninstallOp


def _make_parser(op_cls: type, name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser(name)
    op_cls().setup_parser(sub)
    return parser


class TestInstall:
    def test_setup_parser_no_args(self) -> None:
        parser = _make_parser(InstallOp, "install")
        ns = parser.parse_args(["install"])
        assert ns.cmd == "install"

    def test_run_uses_runpy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``install`` calls ``runpy.run_module('playwright', ...)``."""
        import runpy

        calls: list[tuple[str, str]] = []

        def fake_run_module(module: str, *, run_name: str = None) -> None:
            calls.append((module, run_name))

        monkeypatch.setattr(runpy, "run_module", fake_run_module)

        op = InstallOp()
        parser = _make_parser(InstallOp, "install")
        ns = parser.parse_args(["install"])
        rc = op.run(ns)

        assert rc == 0
        assert calls == [("playwright", "__main__")]


class TestUninstall:
    def test_setup_parser_no_args(self) -> None:
        parser = _make_parser(UninstallOp, "uninstall")
        ns = parser.parse_args(["uninstall"])
        assert ns.cmd == "uninstall"

    def test_run_uses_runpy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``uninstall`` calls ``runpy.run_module('playwright', ...)``."""
        import runpy

        calls: list[tuple[str, str]] = []

        def fake_run_module(module: str, *, run_name: str = None) -> None:
            calls.append((module, run_name))

        monkeypatch.setattr(runpy, "run_module", fake_run_module)

        op = UninstallOp()
        parser = _make_parser(UninstallOp, "uninstall")
        ns = parser.parse_args(["uninstall"])
        rc = op.run(ns)

        assert rc == 0
        assert calls == [("playwright", "__main__")]
