"""CLI-операция ``call-api`` (VSA-rewrite issue #147).

Thin VSA adapter over the ``ConfigAuthSlice``'s API client. The op
issues a raw HTTP request to the given endpoint with the provided
``PARAM=VALUE`` pairs (or a JSON body via ``--data``) and prints the
JSON response.

The slice (with its API client) is constructor-injected.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _ApiClientSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def api_client(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``call-api``."""

    method: str
    endpoint: str
    param: list[str]
    data: Any


class Operation(BaseOperation):
    """Вызвать произвольный метод API <https://github.com/hhru/api>."""

    __aliases__ = ("api",)

    def __init__(self, slice_: _ApiClientSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("endpoint", help="Путь до эндпоинта API")
        parser.add_argument(
            "param",
            nargs="*",
            help="Параметры указываются в виде PARAM=VALUE",
            default=[],
        )
        parser.add_argument(
            "-m", "--method", "--meth", "-X", default="GET", help="HTTP Метод"
        )
        parser.add_argument("-d", "--data", help="JSON строка тела запроса")

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error(
                "call-api requires a ConfigAuthSlice with an api_client"
            )
            return 1

        api_client = slice_.api_client

        as_json = False
        if args.data:
            try:
                params = json.loads(args.data)
                as_json = True
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in --data: {e}")
                return 1
        else:
            params = defaultdict(list)
            for param in args.param:
                key, value = param.split("=", 1)
                params[key].append(value)
            params = dict(params)

        try:
            result = api_client.request(
                args.method,
                args.endpoint,
                params=params,
                as_json=as_json,
            )
            print(json.dumps(result))
            return 0
        except Exception as ex:  # noqa: BLE001 — match legacy behaviour
            logger.debug(ex)
            json.dump(getattr(ex, "data", {"error": str(ex)}), sys.stderr)
            return 1


__all__ = ("Operation", "Namespace")
