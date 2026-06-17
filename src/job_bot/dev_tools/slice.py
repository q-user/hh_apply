"""Dev Tools slice - SQL REPL and CSV/prettytable output (issue #137).

Migrated from ``hh_applicant_tool.operations.query``. The slice is a
small, self-contained vertical — its only collaborator is a
``sqlite3.Connection``, plus an :class:`OutputSink` for rendering.

The factory :func:`create_dev_tools_slice` accepts an already-open
connection; callers that want a fresh in-memory connection (e.g.
tests) can build one themselves. There is no global settings
dependency here, on purpose: the slice is the one place in ``job_bot``
that talks to the local SQLite database directly.
"""

from __future__ import annotations

import sqlite3

from job_bot.dev_tools.handlers.sql_repl_handler import (
    SqlReplHandler,
)
from job_bot.dev_tools.ports.printer_port import OutputSink


class DevToolsSlice:
    """Vertical slice for ad-hoc database inspection.

    Attributes:
        connection: The :class:`sqlite3.Connection` the slice runs
            SQL against. The slice does not own its lifetime.
        sql_repl: The :class:`SqlReplHandler` port exposed to the
            CLI shim (and to any future API consumer).
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        sink: OutputSink | None = None,
    ) -> None:
        self._connection = connection
        self._sql_repl = SqlReplHandler(connection=connection, sink=sink)

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the SQLite connection the slice operates on."""
        return self._connection

    @property
    def sql_repl(self) -> SqlReplHandler:
        """Return the SQL REPL port."""
        return self._sql_repl


def create_dev_tools_slice(
    connection: sqlite3.Connection | None = None,
    sink: OutputSink | None = None,
) -> DevToolsSlice:
    """Factory function to create a :class:`DevToolsSlice`.

    Args:
        connection: SQLite connection to operate on. If ``None``, a
            fresh in-memory connection is created. The factory
            always passes a real connection to the slice.
        sink: Optional :class:`OutputSink` to use for output. Defaults
            to :class:`StdoutSink`.

    Returns:
        Configured :class:`DevToolsSlice` instance.
    """
    if connection is None:
        connection = sqlite3.connect(":memory:")
    return DevToolsSlice(connection=connection, sink=sink)


__all__ = ["DevToolsSlice", "create_dev_tools_slice"]
