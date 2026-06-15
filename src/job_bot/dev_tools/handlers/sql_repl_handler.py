"""SQL REPL handler for the dev_tools slice (issue #137).

Migrated from ``hh_applicant_tool.operations.query``. The handler is
the single point of contact between the legacy CLI and the underlying
``sqlite3.Connection`` — the CLI shim delegates to ``run_repl`` (or
``execute`` for non-interactive use).

The handler is duck-typed on the ``OutputSink`` attribute of the
constructor-injected ``sink``. The default is :class:`StdoutSink`,
which prints to ``sys.stdout`` via ``PrettyTable``/``csv``.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from job_bot.dev_tools.models.query_result import QueryResult
from job_bot.dev_tools.ports.printer_port import OutputSink, StdoutSink

logger = logging.getLogger(__name__)

# Cap on the number of ``SELECT`` rows displayed. Mirrors the legacy
# constant from ``hh_applicant_tool.operations.query``.
MAX_RESULTS = 10

# Allowed output formats. ``table`` (default) renders via
# ``PrettyTable``; ``csv`` uses the standard ``csv`` module.
_FMT_TABLE = "table"
_FMT_CSV = "csv"
_VALID_FMTS = (_FMT_TABLE, _FMT_CSV)


def _default_read_line(_prompt: str) -> str:
    """Read a single line from ``input()`` for the REPL loop."""
    return input(_prompt)


class SqlReplHandler:
    """Execute SQL statements against a ``sqlite3.Connection``.

    Args:
        connection: Live SQLite connection. The handler does **not**
            close it on ``__exit__`` — the owner is responsible.
        sink: Anything implementing the :class:`OutputSink` protocol
            (duck-typed; tests can pass an in-memory recorder).
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        sink: OutputSink | None = None,
    ) -> None:
        self._conn = connection
        self.sink: OutputSink = sink if sink is not None else StdoutSink()

    # ── Public API ───────────────────────────────────────────

    def execute(self, sql_query: str, fmt: str = _FMT_TABLE) -> QueryResult:
        """Run a single SQL statement and emit the result.

        Args:
            sql_query: Raw SQL text. Whitespace-only strings are a
                no-op (returns an empty :class:`QueryResult`).
            fmt: ``"table"`` (default) or ``"csv"``. Mirrors the
                ``--csv`` flag from the legacy CLI.

        Returns:
            A :class:`QueryResult` describing what happened. The
            handler also pushes the rendered output to ``self.sink``
            for the caller to capture.
        """
        sql_query = sql_query.strip()
        if not sql_query:
            return QueryResult()

        if fmt not in _VALID_FMTS:
            raise ValueError(f"fmt must be one of {_VALID_FMTS!r}, got {fmt!r}")

        try:
            cursor = self._conn.cursor()
            cursor.execute(sql_query)
        except sqlite3.Error as ex:
            self.sink.emit_error(f"SQL Error: {ex}")
            return QueryResult(ok=False, error=str(ex))

        # ``cursor.description`` is ``None`` for non-SELECT statements.
        if cursor.description is None:
            # Write statement — commit and report affected rows.
            self._conn.commit()
            if cursor.rowcount > 0:
                self.sink.emit_text(f"Rows affected: {cursor.rowcount}")
            return QueryResult(ok=True, rowcount=cursor.rowcount)

        columns = [d[0] for d in cursor.description]
        # ``fetchmany(MAX_RESULTS + 1)`` lets us cap the output and
        # still detect "more rows available". We materialise each row
        # as a plain ``tuple`` so the :class:`OutputSink` contract is
        # independent of the connection's ``row_factory`` (which the
        # tests set to ``sqlite3.Row``).
        all_rows = [tuple(r) for r in cursor.fetchmany(MAX_RESULTS + 1)]

        if not all_rows:
            self.sink.emit_text("No results found.")
            return QueryResult(ok=True, rowcount=0, columns=columns)

        truncated = len(all_rows) > MAX_RESULTS
        rows_to_show = all_rows[:MAX_RESULTS]

        if fmt == _FMT_CSV:
            self.sink.emit_csv(columns, rows_to_show)
        else:
            self.sink.emit_table(columns, rows_to_show)

        if truncated:
            self.sink.emit_warning(
                f"Warning: Showing only first {MAX_RESULTS} results."
            )

        return QueryResult(
            ok=True,
            rowcount=len(all_rows),
            columns=columns,
            rows=rows_to_show,
        )

    def run_repl(
        self,
        initial_query: str | None = None,
        *,
        read_line: Callable[[str], str] = _default_read_line,
    ) -> None:
        """Drive the interactive SQL REPL loop.

        Args:
            initial_query: If given, execute this once and then exit
                the loop (used by the legacy ``query`` command when
                the user passes a SQL string on the CLI).
            read_line: Replacement for ``input()`` — tests pass a
                deterministic function.
        """
        if initial_query:
            self.execute(initial_query)
            return

        self.sink.emit_text("SQL Console (q or ^D to exit)")

        while True:
            try:
                user_input = read_line("query> ").strip()
            except KeyboardInterrupt:
                # Ctrl-C cancels the current line and re-prompts,
                # matching the legacy CLI's UX.
                self.sink.emit_text("^C")
                continue
            except EOFError:
                # Ctrl-D / piped input exhausted — leave the REPL.
                self.sink.emit_text("")
                return

            if user_input.lower() in ("exit", "quit", "q"):
                return

            self.execute(user_input)
            self.sink.emit_text("")


__all__ = [
    "MAX_RESULTS",
    "SqlReplHandler",
    "StdoutSink",
]
