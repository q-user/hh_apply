"""Tests for the ``settings`` VSA sub-command (issue #147).

The ``settings`` op is a thin VSA adapter over the
:class:`StorageFacade.settings` repository. It supports:
  * set a value (``key VALUE``),
  * get a value (``key``),
  * delete a value (``--delete key``),
  * delete all (``--delete``),
  * list all (default).
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from job_bot.cli.settings import Operation


class _FakeSetting:
    def __init__(self, key: str, value: Any) -> None:
        self.key = key
        self.value = value


class _FakeSettingsRepo:
    def __init__(self, items: list[_FakeSetting] | None = None) -> None:
        self._items = items or []
        self.set_calls: list[tuple[str, Any]] = []
        self.delete_calls: list[str] = []
        self.cleared = False

    def find(self) -> list[_FakeSetting]:
        return list(self._items)

    def get_value(self, key: str, default: Any = None) -> Any:
        for it in self._items:
            if it.key == key:
                return it.value
        return default

    def set_value(self, key: str, value: Any) -> None:
        self.set_calls.append((key, value))
        # Replace-or-insert.
        for it in self._items:
            if it.key == key:
                it.value = value
                return
        self._items.append(_FakeSetting(key, value))

    def delete_value(self, key: str) -> None:
        self.delete_calls.append(key)
        self._items = [it for it in self._items if it.key != key]

    def clear(self) -> None:
        self.cleared = True
        self._items = []


class _FakeSlice:
    def __init__(self, repo: _FakeSettingsRepo) -> None:
        self.settings = repo


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser("settings")
    Operation().setup_parser(sub)
    return parser


class TestSettingsSetupParser:
    def test_default_no_args(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["settings"])
        # The "key" and "value" positionals use a sentinel; we only care
        # about argparse accepting the call.
        assert ns.delete is False

    def test_delete_flag(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["settings", "--delete"])
        assert ns.delete is True


class TestSettingsRun:
    def test_set_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        repo = _FakeSettingsRepo()
        op = Operation(slice_=_FakeSlice(repo))

        parser = _make_parser()
        ns = parser.parse_args(["settings", "user.email", "me@e.com"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert repo.set_calls == [("user.email", "me@e.com")]
        assert "✅" in out or "установл" in out.lower()

    def test_get_value_prints(self, capsys: pytest.CaptureFixture[str]) -> None:
        repo = _FakeSettingsRepo([_FakeSetting("user.email", "me@e.com")])
        op = Operation(slice_=_FakeSlice(repo))

        parser = _make_parser()
        ns = parser.parse_args(["settings", "user.email"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert "me@e.com" in out

    def test_get_missing_key_warns(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _FakeSettingsRepo([])
        op = Operation(slice_=_FakeSlice(repo))

        parser = _make_parser()
        ns = parser.parse_args(["settings", "missing.key"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert "⚠" in out or "не найден" in out.lower()

    def test_delete_specific_key(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _FakeSettingsRepo([_FakeSetting("a", 1), _FakeSetting("b", 2)])
        op = Operation(slice_=_FakeSlice(repo))

        parser = _make_parser()
        ns = parser.parse_args(["settings", "--delete", "a"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert repo.delete_calls == ["a"]
        assert "🗑" in out or "удал" in out.lower()

    def test_delete_all_clears(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _FakeSettingsRepo([_FakeSetting("a", 1), _FakeSetting("b", 2)])
        op = Operation(slice_=_FakeSlice(repo))

        parser = _make_parser()
        ns = parser.parse_args(["settings", "--delete"])
        op.run(ns)

        assert repo.cleared is True
