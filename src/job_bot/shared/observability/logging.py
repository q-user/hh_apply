"""JSON-structured logging for the observability stack (issue #203).

The :class:`JsonFormatter` is a stdlib :class:`logging.Formatter` that
emits *one* JSON document per :class:`logging.LogRecord` -- a stable
shape that a downstream log aggregator (Loki, ELK, CloudWatch) can
parse without writing a custom parser per service.

Design choices
--------------

* **stdlib only.** :mod:`logging` is the only required dependency. The
  formatter is a 30-line subclass; the helper :func:`log_event` is a
  thin wrapper around :func:`logging.Logger.info` that pushes the
  ``event=`` field into the record so the formatter can pick it up.
* **No op on optional deps.** Unlike :mod:`tracing` /
  :mod:`metrics`, the JSON logging path has *no* external runtime
  dependency. It works whether or not the ``observability`` extra is
  installed.
* **Flat top-level keys.** ``timestamp``, ``level``, ``logger``,
  ``message``, ``event``, plus the ``extra={...}`` fields the caller
  passed. Nested objects are kept as-is (one level of nesting
  tolerated) so the aggregator's flattened column mapping stays
  predictable.
* **Exception info as string.** ``exc_info`` is rendered into the
  ``exc_info`` field as a string (not a structured object) because the
  Python exception chain's string repr is already deterministic and
  Grep-friendly.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any, cast

logger = logging.getLogger(__package__)

#: Keys that :class:`logging.LogRecord` fills in from the formatter
#: itself -- anything in this set is *not* forwarded from
#: ``record.__dict__`` because the formatter has already promoted
#: the canonical key (e.g. ``record.msg`` -> ``message``). Otherwise
#: the JSON line would carry duplicates like ``{"message": "...",
#: "msg": "..."}``.
_STDLIB_RESERVED: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Formatter that turns a :class:`LogRecord` into a single JSON line.

    The output shape is::

        {
            "timestamp": "2026-06-18T10:11:12.345678Z",
            "level":     "INFO",
            "logger":    "job_bot.cli.apply_worker",
            "message":   "apply-worker started",
            "event":     "apply_worker_started",
            ...               # plus any ``extra={...}`` fields
        }

    The ``timestamp`` is RFC 3339 / ISO 8601 in UTC with microsecond
    precision, which is what every modern log aggregator expects
    (Loki's ``{job="..."}``-style label set, Elasticsearch's default
    date format, etc.). The trailing ``Z`` is explicit (no implicit
    local-time interpretation downstream).
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 -- stdlib name
        """Return the JSON-encoded string for ``record``.

        Args:
            record: The :class:`logging.LogRecord` to serialize. Its
                ``__dict__`` is scanned for any extra fields the
                caller added via ``logger.info("msg", extra={...})``
                or the :func:`log_event` helper; those become
                top-level JSON keys alongside the canonical ones.

        Returns:
            A single line of JSON (no trailing newline -- the
            :class:`logging.StreamHandler` adds one). The line is
            always valid JSON, even when ``extra={}`` is empty.
        """
        payload: dict[str, Any] = {
            "timestamp": _format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Promote ``event`` first so an explicit ``extra={"event": ...}``
        # always wins over the default.
        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _STDLIB_RESERVED or key.startswith("_"):
                continue
            # The stdlib stashes ``extra`` on the record too; skip it
            # because we already iterated it.
            if key == "extra":
                continue
            extras[key] = _coerce(value)
        payload.update(extras)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_json_logging(
    level: str = "INFO",
    *,
    stream: Any | None = None,
) -> None:
    """Install the :class:`JsonFormatter` on the root logger.

    Idempotent: a second call replaces the previous handler instead
    of stacking a second one. The formatter is the same for the root
    logger (and any existing ``logging.Logger`` descendants); child
    loggers inherit the handler unless they have ``propagate=False``.

    Args:
        level: A level name accepted by :func:`logging.getLevelName`
            (e.g. ``"INFO"``, ``"DEBUG"``, ``"WARNING"``). Invalid
            strings raise :class:`ValueError` from the stdlib.
        stream: Optional output stream. Defaults to
            :data:`sys.stderr` (matches stdlib's default). Tests pass
            an :class:`io.StringIO` to capture the output.

    Why configure the root logger, not a child? Because a CLI
    daemon's third-party loggers (``urllib3``, ``requests``,
    ``apscheduler``, etc.) need to be formatted the same way -- and
    the only place those are guaranteed to bubble up to is the root
    handler. Configuring a child logger would leave them as plain
    text.
    """
    root = logging.getLogger()
    root.setLevel(level)
    # Remove any handlers we previously installed (idempotency).
    for handler in list(root.handlers):
        if getattr(handler, "_job_bot_json", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    # Marker for the idempotency sweep above. ``StreamHandler``
    # doesn't expose this attribute in the stubs; we cast to
    # ``object`` so mypy accepts the arbitrary attribute set.
    cast("object", handler).__setattr__("_job_bot_json", True)
    root.addHandler(handler)


def log_event(name: str, **fields: Any) -> None:
    """Emit a structured log event at ``INFO`` level.

    The :func:`log_event` helper exists so callers don't have to
    remember the stdlib incantation::

        logger.info("event", extra={"event": "foo", "k": "v"})

    A short::

        log_event("foo", k="v")

    expands to the same thing -- the ``name`` is forwarded as the
    ``event=`` field, the remaining kwargs go into ``extra={...}``,
    and the human-readable message stays the bare event name so the
    legacy human-readable loggers see something useful too.

    Args:
        name: The event identifier (e.g. ``"vacancy_processed"``).
            Becomes the ``event=`` JSON key and the log message.
        **fields: Arbitrary structured fields. Keys must be valid
            Python identifiers; values are coerced to a JSON-friendly
            shape by :func:`_coerce` (datetimes -> ISO 8601, etc.).
    """
    extra = {"event": name, **fields}
    logger.info(name, extra=extra)


# ─── Helpers ────────────────────────────────────────────────────


def _format_timestamp(created: float) -> str:
    """Format a POSIX timestamp as an RFC 3339 / ISO 8601 string in UTC.

    Args:
        created: A :func:`time.time`-style float (seconds since the
            epoch). The stdlib :attr:`LogRecord.created` attribute.

    Returns:
        An ISO 8601 string with microsecond precision and a trailing
        ``Z`` (e.g. ``"2026-06-18T10:11:12.345678Z"``). The ``Z``
        makes the timezone explicit so a downstream parser never
        has to guess.
    """
    dt = _dt.datetime.fromtimestamp(created, tz=_dt.timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _coerce(value: Any) -> Any:
    """Coerce ``value`` into something :func:`json.dumps` can serialize.

    Args:
        value: An arbitrary Python object. Strings, ints, floats,
            bools, ``None``, lists, and dicts are passed through
            unchanged. Datetimes / dates are converted to ISO 8601
            so the aggregator can use them as timestamps.

    Returns:
        A JSON-friendly representation. Anything :func:`json.dumps`
        can't handle is left to :func:`json.dumps`'s ``default=``
        callback (which calls ``str(value)``).
    """
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return value


__all__ = [
    "JsonFormatter",
    "configure_json_logging",
    "log_event",
]
