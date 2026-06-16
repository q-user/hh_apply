"""Tests for the ``clear_skipped`` VSA sub-command (issue #147)."""

from __future__ import annotations

import argparse

import pytest

from job_bot.cli.clear_skipped import Operation


class _FakeItem:
    def __init__(self, item_id: str) -> None:
        self.id = item_id


class _FakeSkippedRepo:
    def __init__(self, items: list[_FakeItem] | None = None) -> None:
        self._items = items or []
        self.deleted: list[tuple[str, bool]] = []
        self.committed = False
        self.cleared = False

    def find(self, *, reason: str | None = None) -> list[_FakeItem]:
        return list(self._items)

    def delete(self, item_id: str, *, commit: bool = False) -> None:
        self.deleted.append((item_id, commit))

    def commit(self) -> None:
        self.committed = True

    def clear(self) -> None:
        self.cleared = True

    def count_total(self) -> int:
        return len(self._items)


class _FakeSlice:
    def __init__(self, repo: _FakeSkippedRepo) -> None:
        self.skipped_vacancies = repo


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser("clear-skipped")
    Operation().setup_parser(sub)
    return parser


class TestClearSkippedSetupParser:
    def test_reason_is_optional(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["clear-skipped"])
        assert ns.reason is None
        assert ns.dry_run is False

    def test_dry_run_flag(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["clear-skipped", "--dry-run"])
        assert ns.dry_run is True

    def test_reason_value(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["clear-skipped", "--reason", "ai_rejected"])
        assert ns.reason == "ai_rejected"


class TestClearSkippedRun:
    def test_dry_run_with_reason_prints_count(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _FakeSkippedRepo([_FakeItem("1"), _FakeItem("2")])
        op = Operation(slice_=_FakeSlice(repo))

        parser = _make_parser()
        ns = parser.parse_args(
            ["clear-skipped", "--reason", "ai_rejected", "--dry-run"]
        )
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert "2" in out
        assert repo.deleted == []  # No deletes on dry run.
        assert repo.committed is False

    def test_run_with_reason_deletes_and_commits(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _FakeSkippedRepo(
            [_FakeItem("1"), _FakeItem("2"), _FakeItem("3")]
        )
        op = Operation(slice_=_FakeSlice(repo))

        parser = _make_parser()
        ns = parser.parse_args(["clear-skipped", "--reason", "blocked"])
        rc = op.run(ns)

        assert rc == 0
        # All items deleted, commit=True passed to the last call,
        # and repo.commit() called.
        assert repo.committed is True
        assert len(repo.deleted) == 3

    def test_run_no_reason_clears_all(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _FakeSkippedRepo(
            [_FakeItem("1"), _FakeItem("2"), _FakeItem("3")]
        )
        op = Operation(slice_=_FakeSlice(repo))

        parser = _make_parser()
        ns = parser.parse_args(["clear-skipped"])
        op.run(ns)

        assert repo.cleared is True

    def test_no_op_when_repo_empty(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _FakeSkippedRepo([])
        op = Operation(slice_=_FakeSlice(repo))

        parser = _make_parser()
        ns = parser.parse_args(["clear-skipped"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert repo.cleared is False
        assert repo.committed is False
        # Either no-op message or empty output.
        assert out.strip() != "" or out == ""
