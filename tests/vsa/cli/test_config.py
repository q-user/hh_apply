"""Tests for the ``config`` VSA sub-command (issue #147).

The ``config`` op is a thin VSA adapter over the
:class:`ConfigAuthSlice.config` port. It supports the legacy CLI surface:
  * show the whole config (default),
  * show a single key (``--key``),
  * set a key (``--set``),
  * unset a key (``--unset``),
  * show the config path (``--show-path``),
  * edit the config in ``$EDITOR`` (``--edit``).

The dotted-path KV helpers (get/set/del) live in
:class:`job_bot.config_auth.handlers.config_kv_handler.ConfigKVHandler`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from job_bot.cli.config import Operation


class _FakeKVHandler:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {
            "openai": {"model": "gpt-4o"},
            "telegram": {"bot_token": "abc"},
        }
        self.set_calls: list[tuple[str, Any]] = []
        self.del_calls: list[str] = []
        self.saved = 0

    def get_value(self, data: dict[str, Any], path: str) -> Any:
        node: Any = data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return node

    def set_value(self, data: dict[str, Any], path: str, value: Any) -> None:
        self.set_calls.append((path, value))
        keys = path.split(".")
        for k in keys[:-1]:
            data = data.setdefault(k, {})
        data[keys[-1]] = value

    def del_value(self, data: dict[str, Any], path: str) -> bool:
        self.del_calls.append(path)
        keys = path.split(".")
        for k in keys[:-1]:
            if not isinstance(data, dict) or k not in data:
                return False
            data = data[k]
        try:
            del data[keys[-1]]
        except KeyError:
            return False
        return True

    def parse_scalar(self, value: str) -> Any:
        if value == "null":
            return None
        if value in ("true", "false"):
            return "t" in value
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value

    def save(self) -> None:
        self.saved += 1


class _FakeConfigPort:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {
            "openai": {"model": "gpt-4o"},
            "telegram": {"bot_token": "abc"},
        }
        self.path = Path("/tmp/config.json")
        self.kv = _FakeKVHandler()

    def load(self) -> dict[str, Any]:
        return self.data

    def save(self) -> None:
        self.kv.save()


class _FakeSlice:
    def __init__(self, config: _FakeConfigPort) -> None:
        self.config = config


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser("config")
    Operation().setup_parser(sub)
    return parser


class TestConfigSetupParser:
    def test_default_no_args(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["config"])
        assert ns.key is None
        assert ns.set is None
        assert ns.unset is None
        assert ns.show_path is False
        assert ns.edit is False

    def test_key_value(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["config", "-k", "openai.model"])
        assert ns.key == "openai.model"

    def test_set_takes_two_values(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["config", "-s", "openai.model", "gpt-5"])
        assert ns.set == ["openai.model", "gpt-5"]

    def test_unset_value(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["config", "-u", "openai.model"])
        assert ns.unset == "openai.model"

    def test_show_path(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["config", "-p"])
        assert ns.show_path is True


class TestConfigRun:
    def test_default_prints_full_config(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = _FakeConfigPort()
        op = Operation(slice_=_FakeSlice(config))

        parser = _make_parser()
        ns = parser.parse_args(["config"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        # The full config is dumped as JSON.
        dumped = json.loads(out)
        assert dumped["openai"]["model"] == "gpt-4o"
        assert dumped["telegram"]["bot_token"] == "abc"

    def test_key_prints_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        config = _FakeConfigPort()
        op = Operation(slice_=_FakeSlice(config))

        parser = _make_parser()
        ns = parser.parse_args(["config", "-k", "openai.model"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert out.strip() == "gpt-4o"

    def test_set_writes_value_and_saves(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = _FakeConfigPort()
        op = Operation(slice_=_FakeSlice(config))

        parser = _make_parser()
        ns = parser.parse_args(["config", "-s", "openai.model", "gpt-5"])
        rc = op.run(ns)

        assert rc == 0
        assert config.kv.set_calls == [("openai.model", "gpt-5")]
        assert config.kv.saved == 1

    def test_unset_removes_key(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = _FakeConfigPort()
        op = Operation(slice_=_FakeSlice(config))

        parser = _make_parser()
        ns = parser.parse_args(["config", "-u", "openai.model"])
        rc = op.run(ns)

        assert rc == 0
        assert config.kv.del_calls == ["openai.model"]
        assert config.kv.saved == 1

    def test_show_path_prints_path(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = _FakeConfigPort()
        op = Operation(slice_=_FakeSlice(config))

        parser = _make_parser()
        ns = parser.parse_args(["config", "-p"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert "/tmp/config.json" in out
