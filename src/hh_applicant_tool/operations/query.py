"""SQL REPL/CSV/prettytable CLI for the local SQLite database.

.. deprecated:: 1.9
   Use :class:`job_bot.dev_tools.DevToolsSlice` (or
   :func:`job_bot.dev_tools.create_dev_tools_slice`) instead.
   This module is part of the VSA switchover (issue #137) and
   **planned for removal in version 2.0**.

Legacy module that powered the ``query`` / ``sql`` CLI command.
The body has been migrated to :mod:`job_bot.dev_tools`; this file
is kept as a thin shim that delegates to the VSA slice and emits a
:class:`DeprecationWarning` on instantiation.

Public surface (CLI flags, namespace, aliases) is preserved verbatim
so the existing ``hh-applicant-tool query …`` command continues to
work.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sqlite3
import sys
import warnings
from typing import TYPE_CHECKING

from prettytable import PrettyTable  # noqa: F401  (re-exported for back-compat)

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

# Issue #137: this module is deprecated. The deprecation warning
# fires on instantiation of ``Operation`` (not at import time) so
# that just importing the module for argparse dispatch does not
# pollute every test run.

logger = logging.getLogger(__package__)

# Backwards-compat re-export — the new VSA slice exposes the same
# constant from :mod:`job_bot.dev_tools.handlers.sql_repl_handler`.
MAX_RESULTS = 10


try:
    import readline

    readline.parse_and_bind("tab: complete")
except ImportError:
    readline = None  # type: ignore[assignment]


class Namespace(BaseNamespace):
    """Backwards-compat namespace — the VSA slice does not own CLI args."""

    pass


class Operation(BaseOperation):
    """Thin shim that delegates to :class:`job_bot.dev_tools.DevToolsSlice`.

    Public surface preserved verbatim from the legacy implementation:

    * ``__aliases__`` — ``["sql"]``.
    * ``setup_parser`` — ``sql``, ``--csv``, ``-o/--output`` flags.
    * ``run`` — dispatch to the VSA SQL REPL.
    """

    __aliases__: list[str] = ["sql"]

    def __init__(self) -> None:
        warnings.warn(
            "hh_applicant_tool.operations.query is deprecated; "
            "use job_bot.dev_tools instead (issue #137).",
            DeprecationWarning,
            stacklevel=2,
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("sql", nargs="?", help="SQL запрос")
        parser.add_argument(
            "--csv",
            action="store_true",
            help="Вывести результат в формате CSV",
        )
        parser.add_argument(
            "-o",
            "--output",
            type=pathlib.Path,
            help="Файл для сохранения",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> int | None:
        """Dispatch the legacy ``query`` command to the VSA SQL REPL.

        The legacy operator supported three modes:

        * interactive REPL (no ``sql`` argument, stdin is a tty),
        * one-shot execution from a CLI argument,
        * one-shot execution from a piped stdin (legacy branch).

        The VSA slice's :meth:`SqlReplHandler.run_repl` covers all
        three — it takes an ``initial_query`` and a custom
        ``read_line`` callback.
        """
        from job_bot.dev_tools.handlers.sql_repl_handler import StdoutSink
        from job_bot.dev_tools.slice import create_dev_tools_slice

        # When ``-o path`` is given, route CSV into a file-backed
        # stdout sink. This preserves the legacy "✅  Exported to …"
        # UX without duplicating the writer.
        if args.output is not None:
            stream = args.output.open("w", encoding="utf-8")
            sink = StdoutSink(stream=stream)
            slice_ = create_dev_tools_slice(connection=tool.db, sink=sink)
        else:
            slice_ = create_dev_tools_slice(connection=tool.db)

        initial_query: str | None = None
        if args.sql:
            initial_query = args.sql
        elif not sys.stdin.isatty():
            initial_query = sys.stdin.read()

        try:
            slice_.sql_repl.run_repl(initial_query=initial_query)
        except sqlite3.Error as ex:
            # The slice already pushed the error to its sink; emit a
            # non-zero exit code to match the legacy operator's
            # contract.
            print(f"❌  SQL Error: {ex}")
            return 1

        if args.output is not None:
            print(f"✅  Exported to {args.output.name}")
        return None
