"""Domain models for the dev_tools slice (issue #137).

The :class:`QueryResult` dataclass is the only "model" produced by
the slice — every SQL REPL call returns one, regardless of whether
the query was a ``SELECT`` (rows), a write statement (rowcount), or
a parse error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryResult:
    """Outcome of a single ``SqlReplHandler.execute`` call.

    Attributes:
        ok: ``True`` when the statement ran without an error.
        error: Human-readable error message (only set when ``ok`` is
            ``False``).
        rowcount: Number of rows affected (write) or returned
            (``SELECT``). ``None`` when the result was empty or
            undeterminable.
        columns: Column names for a ``SELECT`` result; empty for
            write statements.
        rows: Row tuples for a ``SELECT`` result; empty for writes.
    """

    ok: bool = True
    error: str | None = None
    rowcount: int | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)


__all__ = ["QueryResult"]
