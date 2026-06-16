"""CLI-операция ``install`` (VSA-rewrite issue #147).

Thin ``subprocess`` wrapper: invokes ``playwright install chromium``.
The op has no VSA dependency — it's a one-liner around :mod:`runpy`.

The dispatcher in :class:`BUILTIN_OPERATIONS` keeps it as a no-deps
op for parity with the legacy behaviour.

The ``runpy.run_module`` call is dispatched via attribute lookup
(``runpy.run_module(...)``) rather than a top-level ``from runpy
import run_module`` so the unit tests can
:func:`monkeypatch.setattr` ``runpy.run_module`` and observe the call.
"""

from __future__ import annotations

import argparse
import logging
import runpy
import sys

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    """Аргументы ``install`` (no extra fields)."""


class Operation(BaseOperation):
    """Установит Chromium и другие зависимости."""

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace) -> int:
        orig_argv = sys.argv
        sys.argv = ["playwright", "install", "chromium"]
        try:
            runpy.run_module("playwright", run_name="__main__")
        finally:
            sys.argv = orig_argv
        return 0


__all__ = ("Operation", "Namespace")
