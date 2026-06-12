"""Date-aware JSON helpers (legacy — internal to ``utils.config``).

Re-exports :mod:`json` with a :class:`JSONEncoder` that converts
``datetime`` values to POSIX timestamps so they can be stored in
JSON. Imported by :mod:`hh_applicant_tool.utils.config` via
``from . import json``.
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
