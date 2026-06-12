"""Tests for the CLI ``channel-monitor`` operation (issue #57).

Covers the CLI surface owned by :class:`Operation`:

* argparse flags ``--list``, ``--add``, ``--remove``, ``--parse``;
* ``--list`` with and without ``--enabled``;
* ``--add`` writes via the slice and prints the new channel JSON;
* ``--remove`` returns 1 when the channel is missing;
* ``--parse`` returns the extracted vacancy links as JSON;
* the ``Operation`` accepts a ``slice_`` (DI-injection from tests /
  the container) — when ``None`` it builds one from ``tool.db``.

Shared helpers (``_SimpleTool``, ``_make_args``) live in
:mod:`tests.conftest`.
"""

from __future__ import annotations

import argparse
import sqlite3
from typing import Any

import pytest

from hh_applicant_tool.operations.channel_monitor import Operation
from job_bot.channel_monitoring.handlers.channel_handler import ChannelHandler
from job_bot.channel_monitoring.models.channel import ChannelCreate
from job_bot.channel_monitoring.slice import ChannelMonitorSlice

# ``_make_args`` for channel-monitor has extra fields, so it lives in
# this file (parallel to the ``_make_args`` in ``conftest.py``).


# ─── Helpers ─────────────────────────────────────────────────────


def _make_tool(storage_conn: sqlite3.Connection) -> Any:
    """Build a minimal mock ``HHApplicantTool`` (just ``.db``)."""
    tool = MagicMockLite()
    tool.db = storage_conn
    return tool


class MagicMockLite:
    """A bare-bones stand-in for ``HHApplicantTool``."""

    def __init__(self) -> None:
        self.db: sqlite3.Connection | None = None


def _make_slice(storage_conn: sqlite3.Connection) -> ChannelMonitorSlice:
    from job_bot.channel_monitoring.slice import create_channel_monitor_slice

    return create_channel_monitor_slice(conn=storage_conn)


def _make_args(
    *,
    list_: bool = False,
    enabled: bool = False,
    add: bool = False,
    name: str | None = None,
    channel_id: str | None = None,
    keywords: str | None = None,
    remove: bool = False,
    parse: bool = False,
    text: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        list=list_,
        enabled=enabled,
        add=add,
        name=name,
        channel_id=channel_id,
        keywords=keywords,
        remove=remove,
        parse=parse,
        text=text,
        profile_id="default",
        config_dir=None,
        verbosity=0,
        api_delay=None,
        user_agent=None,
        proxy_url=None,
        openai_proxy_url=None,
        operation_run=None,
    )


# ─── CLI: argument parsing ───────────────────────────────────────


class _ParserHost:
    """Minimal host for :meth:`Operation.setup_parser`."""

    def __init__(self) -> None:
        self.parser = argparse.ArgumentParser()
        Operation().setup_parser(self.parser)


def test_cli_flag_list_is_store_true() -> None:
    host = _ParserHost()
    assert host.parser.parse_args([]).list is False
    assert host.parser.parse_args(["--list"]).list is True


def test_cli_flag_add_is_store_true() -> None:
    host = _ParserHost()
    assert host.parser.parse_args([]).add is False
    assert host.parser.parse_args(["--add"]).add is True


def test_cli_flag_remove_is_store_true() -> None:
    host = _ParserHost()
    assert host.parser.parse_args([]).remove is False
    assert host.parser.parse_args(["--remove"]).remove is True


def test_cli_flag_parse_is_store_true() -> None:
    host = _ParserHost()
    assert host.parser.parse_args([]).parse is False
    assert host.parser.parse_args(["--parse"]).parse is True


def test_cli_flag_enabled_is_store_true() -> None:
    host = _ParserHost()
    assert host.parser.parse_args([]).enabled is False
    assert host.parser.parse_args(["--list", "--enabled"]).enabled is True


# ─── --list ───────────────────────────────────────────────────────


def test_list_returns_zero_with_empty_slice(
    storage: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    op = Operation(slice_=_make_slice(storage))
    tool = _make_tool(storage)
    rc = op.run(tool, _make_args(list_=True))  # type: ignore[arg-type]
    assert rc == 0
    out = capsys.readouterr().out
    assert "Нет отслеживаемых каналов" in out


def test_list_prints_channels(
    storage: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    slice_ = _make_slice(storage)
    slice_.channels.add_channel(ChannelCreate(name="Vac", channel_id="@vac"))
    op = Operation(slice_=slice_)
    tool = _make_tool(storage)
    rc = op.run(tool, _make_args(list_=True))  # type: ignore[arg-type]
    assert rc == 0
    out = capsys.readouterr().out
    assert "Vac" in out
    assert "@vac" in out


def test_list_enabled_filters_disabled(
    storage: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--list --enabled`` only prints enabled channels (exits 0)."""
    slice_ = _make_slice(storage)
    slice_.channels.add_channel(
        ChannelCreate(name="A", channel_id="@a", enabled=True)
    )
    slice_.channels.add_channel(
        ChannelCreate(name="B", channel_id="@b", enabled=False)
    )

    op = Operation(slice_=slice_)
    tool = _make_tool(storage)
    rc = op.run(
        tool,
        _make_args(list_=True, enabled=True),  # type: ignore[arg-type]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "@a" in out
    assert "@b" not in out


# ─── --add ────────────────────────────────────────────────────────


def test_add_requires_name_and_channel_id(
    storage: sqlite3.Connection,
) -> None:
    op = Operation(slice_=_make_slice(storage))
    tool = _make_tool(storage)
    rc = op.run(tool, _make_args(add=True, name=None, channel_id="@x"))  # type: ignore[arg-type]
    assert rc == 1


def test_add_persists_and_prints_json(
    storage: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    op = Operation(slice_=_make_slice(storage))
    tool = _make_tool(storage)
    rc = op.run(
        tool,
        _make_args(
            add=True,
            name="Vac",
            channel_id="@vac",
            keywords="python,django",
        ),  # type: ignore[arg-type]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert '"name": "Vac"' in out
    assert '"channel_id": "@vac"' in out
    assert "python" in out and "django" in out


# ─── --remove ────────────────────────────────────────────────────


def test_remove_missing_returns_1(storage: sqlite3.Connection) -> None:
    op = Operation(slice_=_make_slice(storage))
    tool = _make_tool(storage)
    rc = op.run(
        tool,
        _make_args(remove=True, channel_id="@missing"),  # type: ignore[arg-type]
    )
    assert rc == 1


def test_remove_existing_returns_0(
    storage: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    slice_ = _make_slice(storage)
    slice_.channels.add_channel(ChannelCreate(name="X", channel_id="@x"))

    op = Operation(slice_=slice_)
    tool = _make_tool(storage)
    rc = op.run(
        tool,
        _make_args(remove=True, channel_id="@x"),  # type: ignore[arg-type]
    )
    assert rc == 0
    assert "Удалён канал @x" in capsys.readouterr().out
    assert slice_.channels.get_channel("@x") is None


def test_remove_requires_channel_id(storage: sqlite3.Connection) -> None:
    op = Operation(slice_=_make_slice(storage))
    tool = _make_tool(storage)
    rc = op.run(tool, _make_args(remove=True, channel_id=None))  # type: ignore[arg-type]
    assert rc == 1


# ─── --parse ─────────────────────────────────────────────────────


def test_parse_with_text_extracts_links(
    storage: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    op = Operation(slice_=_make_slice(storage))
    tool = _make_tool(storage)
    rc = op.run(
        tool,
        _make_args(parse=True, text="Apply: https://hh.ru/vacancy/12345"),  # type: ignore[arg-type]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "12345" in out
    assert "hh.ru/vacancy/12345" in out


def test_parse_with_no_text_returns_1(storage: sqlite3.Connection) -> None:
    op = Operation(slice_=_make_slice(storage))
    tool = _make_tool(storage)
    rc = op.run(tool, _make_args(parse=True, text=None))  # type: ignore[arg-type]
    assert rc == 1


def test_parse_with_no_links_prints_no_match(
    storage: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    op = Operation(slice_=_make_slice(storage))
    tool = _make_tool(storage)
    rc = op.run(
        tool,
        _make_args(parse=True, text="hello world"),  # type: ignore[arg-type]
    )
    assert rc == 0
    assert "не найдены" in capsys.readouterr().out


# ─── DI injection ───────────────────────────────────────────────


def test_operation_accepts_slice() -> None:
    """``Operation(slice_=...)`` uses the injected slice as-is."""
    fake_slice = object()  # any object works; the operation forwards to it
    op = Operation(slice_=fake_slice)
    assert op._slice is fake_slice  # type: ignore[attr-defined]


def test_build_slice_uses_tool_db(storage: sqlite3.Connection) -> None:
    """Without an injected slice, ``_build_slice`` reads from ``tool.db``."""
    op = Operation()
    tool = _make_tool(storage)
    built = op._build_slice(tool)  # type: ignore[arg-type]
    assert isinstance(built, ChannelMonitorSlice)
    assert isinstance(built.handler, ChannelHandler)


# ─── Slice surface sanity ───────────────────────────────────────


def test_slice_handler_exposes_channels() -> None:
    slice_ = _make_slice(_fresh_conn())
    assert slice_.channels is slice_.handler


def _fresh_conn() -> sqlite3.Connection:
    """An empty in-memory SQLite connection (no schema)."""
    return sqlite3.connect(":memory:")


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
