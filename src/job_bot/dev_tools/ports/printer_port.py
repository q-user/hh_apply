"""Output sink protocol for the dev_tools slice (issue #137).

The dev_tools slice was migrated from ``hh_applicant_tool.operations.query``
which printed directly to ``sys.stdout``. The VSA slice talks to an
:class:`OutputSink` port instead so that:

* tests can capture emissions via an in-memory fake (no real stdout),
* the slice can be embedded in a notebook, web view, or CI log handler
  without a hard dependency on stdout.

Both ``StdoutSink`` (default) and a real ``OutputSink`` Protocol live
in the same module — the Protocol is used for ``isinstance`` checks,
the class is the production default.
"""

from __future__ import annotations

import csv
import sys
from typing import Any, Protocol, runtime_checkable

from prettytable import PrettyTable


@runtime_checkable
class OutputSink(Protocol):
    """Sink for human-readable and machine-readable dev_tools output."""

    def emit_text(self, text: str) -> None:
        """Emit a free-form text line (e.g. ``"No results found."``)."""
        ...

    def emit_warning(self, text: str) -> None:
        """Emit a non-fatal warning (e.g. row truncation notice)."""
        ...

    def emit_error(self, text: str) -> None:
        """Emit an error message (e.g. ``"SQL Error: no such table"``)."""
        ...

    def emit_table(
        self, columns: list[str], rows: list[tuple[Any, ...]]
    ) -> None:
        """Emit a ``SELECT`` result as a human-readable table."""
        ...

    def emit_csv(self, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        """Emit a ``SELECT`` result as CSV (no header in the file is added)."""
        ...


class StdoutSink:
    """Default :class:`OutputSink` that prints to ``sys.stdout``.

    PrettyTable is used for ``emit_table``; the standard ``csv`` module
    is used for ``emit_csv``. Each emission is suffixed with a newline
    when one is not already present.
    """

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def _writeln(self, text: str) -> None:
        if not text.endswith("\n"):
            text = text + "\n"
        self._stream.write(text)

    def emit_text(self, text: str) -> None:
        self._writeln(text)

    def emit_warning(self, text: str) -> None:
        self._writeln(text)

    def emit_error(self, text: str) -> None:
        self._writeln(text)

    def emit_table(
        self, columns: list[str], rows: list[tuple[Any, ...]]
    ) -> None:
        table = PrettyTable()
        table.field_names = list(columns)
        for row in rows:
            table.add_row(list(row))
        self._writeln(str(table))

    def emit_csv(self, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        writer = csv.writer(self._stream)
        writer.writerow(list(columns))
        writer.writerows(list(rows))
        # ``csv.writer`` doesn't add a trailing newline on the last row.
        if rows:
            self._writeln("")


__all__ = ["OutputSink", "StdoutSink"]
