"""Dev Tools slice - ad-hoc database inspection (issue #137).

Migrated from ``hh_applicant_tool.operations.query``. The slice
exposes a small SQL REPL that can render results as a pretty table
or as CSV.

Public surface:

* :class:`DevToolsSlice` — the slice container.
* :func:`create_dev_tools_slice` — factory function.
* :class:`SqlReplHandler` — the underlying handler (use the slice
  unless you need a tighter DI surface).
* :class:`OutputSink` / :class:`StdoutSink` — output protocol and
  default implementation.
* :class:`QueryResult` — return value of a single ``execute`` call.
"""

from job_bot.dev_tools.handlers.sql_repl_handler import (
    MAX_RESULTS,
    SqlReplHandler,
    StdoutSink,
)
from job_bot.dev_tools.models.query_result import QueryResult
from job_bot.dev_tools.ports.printer_port import OutputSink
from job_bot.dev_tools.slice import (
    DevToolsSlice,
    create_dev_tools_slice,
)

__all__ = [
    "DevToolsSlice",
    "create_dev_tools_slice",
    "MAX_RESULTS",
    "OutputSink",
    "QueryResult",
    "SqlReplHandler",
    "StdoutSink",
]
