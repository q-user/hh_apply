"""CLI-операция ``check-proxy`` (VSA-rewrite issue #147).

Sends a ``GET`` request to ``https://icanhazip.com`` using the slice's
HTTP session (which is configured with the user-specified proxy) and
prints the apparent IP.

The slice is constructor-injected.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _SessionSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def session(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``check-proxy`` (no extra fields)."""


class Operation(BaseOperation):
    """Проверить прокси."""

    __aliases__: list[str] = []

    def __init__(self, slice_: _SessionSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("check-proxy requires a slice with a session")
            return 1
        session = slice_.session
        if not getattr(session, "proxies", None):
            logger.error("Прокси не заданы")
            return 1
        print(session.get("https://icanhazip.com").text)
        return 0


__all__ = ("Operation", "Namespace")
