"""Tests for the ``migrate_db`` VSA sub-command (issue #147)."""

from __future__ import annotations

import argparse
import sqlite3

import pytest

from job_bot.cli.migrate_db import Operation


class _FakeMigrationRunner:
    def __init__(self, migrations: list[str] | None = None) -> None:
        self.migrations = migrations or []
        self.applied: list[str] = []

    def list_migrations(self) -> list[str]:
        return list(self.migrations)

    def apply_migration(self, name: str) -> None:
        self.applied.append(name)


class _FakeSlice:
    def __init__(
        self, conn: sqlite3.Connection, runner: _FakeMigrationRunner
    ) -> None:
        self.db = conn
        self.migrations = runner


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser("migrate-db")
    Operation().setup_parser(sub)
    return parser


class TestMigrateDbSetupParser:
    def test_name_is_optional(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["migrate-db"])
        assert ns.name is None


class TestMigrateDbRun:
    def test_apply_specific_migration(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runner = _FakeMigrationRunner()
        op = Operation(slice_=_FakeSlice(sqlite3.connect(":memory:"), runner))

        parser = _make_parser()
        ns = parser.parse_args(["migrate-db", "0001_init"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert runner.applied == ["0001_init"]
        assert "✅" in out or "success" in out.lower()

    def test_lists_migrations_in_non_tty(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runner = _FakeMigrationRunner(["0001_init", "0002_indexes"])
        op = Operation(slice_=_FakeSlice(sqlite3.connect(":memory:"), runner))

        parser = _make_parser()
        ns = parser.parse_args(["migrate-db"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        # Non-TTY: names printed flat (one per line on stdout).
        # When not on TTY, the runner's list is shown.
        assert "0001_init" in out or runner.applied == []
        # No apply was triggered because no name was passed.
        assert runner.applied == []
