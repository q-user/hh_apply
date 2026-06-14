"""Date/time parsing helpers used across VSA slices.

Mirrors the legacy :mod:`hh_applicant_tool.utils.date` module. The
public API is intentionally tiny: a shared datetime format constant
and two forgiving parsers used by both the legacy storage models and
VSA repositories.

HH.ru's API uses the ``%Y-%m-%dT%H:%M:%S%z`` format ("Z" or numeric
offset). The fallback ``datetime.fromisoformat`` accepts the same
format on Python 3.11+ for ``+00:00``-style offsets; we keep the
``strptime`` fallback for ``Z``-style offsets.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def parse_api_datetime(dt: str) -> datetime:
    """Parse an HH.ru API datetime string (``%Y-%m-%dT%H:%M:%S%z``)."""
    return datetime.strptime(dt, DATETIME_FORMAT)


def try_parse_datetime(dt: Any) -> datetime | Any:
    """Best-effort parse: ISO first, then HH format, else return ``dt`` as-is.

    Returns the original value (un-parsed) when no parser matches, so
    callers can distinguish "no date" from "bad date".
    """
    for parse in (datetime.fromisoformat, parse_api_datetime):
        try:
            return parse(dt)
        except (ValueError, TypeError):
            pass
    return dt


__all__ = [
    "DATETIME_FORMAT",
    "parse_api_datetime",
    "try_parse_datetime",
]
