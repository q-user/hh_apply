"""Tests for the dev_tools VSA slice (issue #137).

Covers the SQL REPL / CSV / prettytable workflow migrated from
``hh_applicant_tool.operations.query`` into a self-contained vertical
slice. All external boundaries (HH API, stdout) are replaced with
in-memory fakes — only the ``sqlite3`` connection is real.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from job_bot.dev_tools.handlers.sql_repl_handler import (
    MAX_RESULTS,
    SqlReplHandler,
)
from job_bot.dev_tools.ports.printer_port import OutputSink
from job_bot.dev_tools.slice import (
    DevToolsSlice,
    create_dev_tools_slice,
)

# ─── In-memory fakes ─────────────────────────────────────────


class _FakeSink:
    """In-memory :class:`OutputSink` that records everything it receives.

    Each ``emit()`` call appends a ``(kind, payload)`` tuple to
    :attr:`records`. ``emit_csv`` and ``emit_table`` are just
    specialised ``emit``s — they share the same record format.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, Any]] = []

    def emit(self, kind: str, payload: Any) -> None:
        self.records.append((kind, payload))

    def emit_table(
        self, columns: list[str], rows: list[tuple[Any, ...]]
    ) -> None:
        self.records.append(("table", {"columns": columns, "rows": rows}))

    def emit_csv(self, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        self.records.append(("csv", {"columns": columns, "rows": rows}))

    def emit_text(self, text: str) -> None:
        self.records.append(("text", text))

    def emit_warning(self, text: str) -> None:
        self.records.append(("warning", text))

    def emit_error(self, text: str) -> None:
        self.records.append(("error", text))


@pytest.fixture
def sink() -> _FakeSink:
    return _FakeSink()


@pytest.fixture
def sqlite_conn() -> Iterator[sqlite3.Connection]:
    """Fresh in-memory SQLite with a tiny ``items`` table pre-populated."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, qty INTEGER)"
    )
    conn.executemany(
        "INSERT INTO items (name, qty) VALUES (?, ?)",
        [("alpha", 1), ("beta", 2), ("gamma", 3)],
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def handler(sqlite_conn: sqlite3.Connection, sink: _FakeSink) -> SqlReplHandler:
    return SqlReplHandler(connection=sqlite_conn, sink=sink)


# ─── Slice / factory wiring ───────────────────────────────────


class TestDevToolsSlice:
    """Smoke tests for the slice container + factory."""

    def test_create_slice(self, sqlite_conn: sqlite3.Connection) -> None:
        slice_ = DevToolsSlice(connection=sqlite_conn)
        assert slice_.connection is sqlite_conn
        # Default factory should still expose a port the caller can use.
        assert slice_.sql_repl is not None

    def test_create_slice_with_custom_sink(
        self, sqlite_conn: sqlite3.Connection, sink: _FakeSink
    ) -> None:
        slice_ = DevToolsSlice(connection=sqlite_conn, sink=sink)
        assert slice_.sql_repl.sink is sink

    def test_factory_returns_configured_slice(
        self, sqlite_conn: sqlite3.Connection, sink: _FakeSink
    ) -> None:
        slice_ = create_dev_tools_slice(connection=sqlite_conn, sink=sink)
        assert isinstance(slice_, DevToolsSlice)
        assert slice_.sql_repl.sink is sink

    def test_factory_uses_default_sink_when_omitted(
        self, sqlite_conn: sqlite3.Connection
    ) -> None:
        slice_ = create_dev_tools_slice(connection=sqlite_conn)
        # Default sink should be a real OutputSink (StdoutSink or similar).
        assert slice_.sql_repl.sink is not None
        # Must at minimum implement the protocol.
        assert isinstance(slice_.sql_repl.sink, OutputSink)


# ─── SQL execution: table output ──────────────────────────────


class TestSqlReplHandlerTableOutput:
    """``SELECT`` queries → prettytable-style table output."""

    def test_select_renders_table(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        result = handler.execute("SELECT id, name FROM items ORDER BY id")
        assert result.ok is True
        assert result.rowcount == 3
        # The handler should have emitted exactly one table with the columns
        # and the rows (the fake sink is the only place output goes).
        tables = [r for r in sink.records if r[0] == "table"]
        assert len(tables) == 1
        kind, payload = tables[0]
        assert kind == "table"
        assert payload["columns"] == ["id", "name"]
        assert payload["rows"] == [
            (1, "alpha"),
            (2, "beta"),
            (3, "gamma"),
        ]

    def test_select_truncates_at_max_results(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        # Insert > MAX_RESULTS rows so the truncation branch fires.
        big_conn = handler._conn  # type: ignore[attr-defined]
        big_conn.executemany(
            "INSERT INTO items (name, qty) VALUES (?, ?)",
            [(f"row{i}", i) for i in range(MAX_RESULTS + 5)],
        )
        big_conn.commit()

        handler.execute("SELECT id, name FROM items ORDER BY id")

        warnings = [r for r in sink.records if r[0] == "warning"]
        assert warnings, "expected a truncation warning when over MAX_RESULTS"
        tables = [r for r in sink.records if r[0] == "table"]
        assert len(tables) == 1
        # The table is capped at MAX_RESULTS rows.
        assert len(tables[0][1]["rows"]) == MAX_RESULTS

    def test_select_no_results_emits_text(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        result = handler.execute("SELECT id, name FROM items WHERE id = 999")
        assert result.ok is True
        assert result.rowcount == 0
        # The legacy operator prints "No results found." — the handler
        # should surface that as a text emission, not a table.
        text_emissions = [r for r in sink.records if r[0] == "text"]
        assert any("No results" in str(p) for _, p in text_emissions)

    def test_empty_query_is_a_noop(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        result = handler.execute("   ")
        assert result.ok is True
        # Nothing should have been written to the sink.
        assert sink.records == []


# ─── SQL execution: write / commit path ───────────────────────


class TestSqlReplHandlerWritePath:
    """``INSERT``/``UPDATE``/``DELETE`` → commit + affected-rows text."""

    def test_insert_commits_and_reports_rows(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        result = handler.execute(
            "INSERT INTO items (name, qty) VALUES ('delta', 4)"
        )
        assert result.ok is True
        assert result.rowcount == 1
        text_emissions = [r for r in sink.records if r[0] == "text"]
        assert any("Rows affected" in str(p) for _, p in text_emissions)
        # Side effect: the new row is actually persisted.
        count = handler._conn.execute(  # type: ignore[attr-defined]
            "SELECT COUNT(*) FROM items"
        ).fetchone()[0]
        assert count == 4

    def test_sql_error_returns_failure(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        result = handler.execute("SELECT * FROM no_such_table")
        assert result.ok is False
        assert result.error is not None
        errors = [r for r in sink.records if r[0] == "error"]
        assert errors, "expected an error emission on failed query"

    def test_syntax_error_returns_failure(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        result = handler.execute("SELEC BROKEN")
        assert result.ok is False
        assert result.error is not None


# ─── SQL execution: CSV output ────────────────────────────────


class TestSqlReplHandlerCsvOutput:
    """``--csv`` / ``--output`` flag → CSV sink."""

    def test_csv_emission(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        result = handler.execute(
            "SELECT id, name FROM items ORDER BY id", fmt="csv"
        )
        assert result.ok is True
        csvs = [r for r in sink.records if r[0] == "csv"]
        assert len(csvs) == 1
        _, payload = csvs[0]
        assert payload["columns"] == ["id", "name"]
        assert payload["rows"] == [
            (1, "alpha"),
            (2, "beta"),
            (3, "gamma"),
        ]


# ─── REPL loop driver ─────────────────────────────────────────


class TestSqlReplLoop:
    """The :class:`SqlReplHandler` exposes a ``run_repl`` driver used by the
    shim's REPL loop. The driver takes a list of lines + a fake input
    function so we can drive the REPL deterministically.
    """

    def test_repl_processes_lines_until_quit(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        inputs = iter(
            [
                "SELECT id, name FROM items ORDER BY id",
                "q",
            ]
        )
        handler.run_repl(read_line=lambda _: next(inputs))
        # We should see one table emission from the SELECT.
        tables = [r for r in sink.records if r[0] == "table"]
        assert len(tables) == 1
        assert tables[0][1]["rows"] == [
            (1, "alpha"),
            (2, "beta"),
            (3, "gamma"),
        ]

    def test_repl_handles_initial_query(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        handler.run_repl(
            initial_query="SELECT id, name FROM items WHERE id = 1",
            read_line=lambda _: "q",
        )
        tables = [r for r in sink.records if r[0] == "table"]
        assert len(tables) == 1
        assert tables[0][1]["rows"] == [(1, "alpha")]

    def test_repl_swallows_keyboard_interrupt(
        self, handler: SqlReplHandler, sink: _FakeSink
    ) -> None:
        # First call raises KeyboardInterrupt, second quits.
        calls = {"n": 0}

        def fake_read(_: object) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt
            return "q"

        # Should not raise — KeyboardInterrupt is caught and the loop
        # continues. A second Ctrl-C should still let the REPL exit.
        handler.run_repl(read_line=fake_read)
        assert calls["n"] >= 2


# ─── Sink protocol (sanity) ───────────────────────────────────


def test_default_sink_satisfies_protocol() -> None:
    """The default stdout sink must implement the :class:`OutputSink` protocol.

    We don't assert on the side effect (printing); we just make sure
    the protocol methods exist.
    """
    from job_bot.dev_tools.handlers.sql_repl_handler import StdoutSink

    sink = StdoutSink()
    # Method existence check via isinstance on the protocol.
    assert isinstance(sink, OutputSink)


def test_handler_accepts_any_object_with_sink_attribute() -> None:
    """Handlers should be duck-typed on the ``sink`` attribute.

    The shim's printer port is a ``Protocol``; we just confirm the
    handler doesn't enforce the type beyond reading ``.sink`` at
    call time.
    """
    fake = MagicMock(spec=OutputSink)
    h = SqlReplHandler(connection=sqlite3.connect(":memory:"), sink=fake)
    assert h.sink is fake
