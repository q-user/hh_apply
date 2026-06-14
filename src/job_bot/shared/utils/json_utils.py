"""Date-aware JSON helpers used across the project.

Mirrors the legacy :mod:`hh_applicant_tool.utils.json` module.
Wraps the standard library with a :class:`JSONEncoder` that
serialises :class:`datetime.datetime` values to POSIX timestamps, so
they can be round-tripped through the HH.ru API and our SQLite
storage. ``ensure_ascii=False`` is set by default to keep Cyrillic
legible in stored payloads.

The :class:`JSONDecoder` is provided as a no-op subclass of the
stdlib decoder purely for backward compatibility with call sites
that instantiate it explicitly (``JSONDecoder()`` is a valid pattern
in the legacy ``apply_to_vacancies`` and ``vacancy_fetcher`` paths).
"""

from __future__ import annotations

import datetime as dt
import json as _stdlib_json
from typing import Any


class JSONEncoder(_stdlib_json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, dt.datetime):
            return int(o.timestamp())
        return super().default(o)


class JSONDecoder(_stdlib_json.JSONDecoder):
    """Default JSON decoder — provided for backward compatibility."""


def dump(obj: Any, fp: Any, *args: Any, **kwargs: Any) -> None:
    kwargs.setdefault("cls", JSONEncoder)
    kwargs.setdefault("ensure_ascii", False)
    _stdlib_json.dump(obj, fp, *args, **kwargs)


def dumps(obj: Any, *args: Any, **kwargs: Any) -> str:
    kwargs.setdefault("cls", JSONEncoder)
    kwargs.setdefault("ensure_ascii", False)
    return _stdlib_json.dumps(obj, *args, **kwargs)


def load(fp: Any, *args: Any, **kwargs: Any) -> Any:
    return _stdlib_json.load(fp, *args, **kwargs)


def loads(s: str | bytes, *args: Any, **kwargs: Any) -> Any:
    return _stdlib_json.loads(s, *args, **kwargs)


__all__ = ["JSONEncoder", "JSONDecoder", "dump", "dumps", "load", "loads"]
