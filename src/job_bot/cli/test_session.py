"""CLI-операция ``test-session`` (VSA-rewrite issue #147).

Hits ``https://hh.ru`` with the slice's session and prints the
``login:`` field extracted from the page (or warns when not logged in).

The slice is constructor-injected.
"""

from __future__ import annotations

import argparse
import logging
import re
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _SessionSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def session(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``test-session`` (no extra fields)."""


class Operation(BaseOperation):
    """Проверка браузерной сессии, полученной при авторизации."""

    __aliases__: list[str] = []

    def __init__(self, slice_: _SessionSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("test-session requires a slice with a session")
            return 1
        r = slice_.session.get("https://hh.ru")

        if m := re.search(r'^\s+login: "([^"]+)', r.text, re.MULTILINE):
            print("Вы вошли как", m.group(1))
        else:
            logger.warning("Вы не авторизованы!")
        return 0


__all__ = ("Operation", "Namespace")
