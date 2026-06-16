"""CLI-операция ``uninstall`` (VSA-rewrite issue #147).

Thin ``subprocess`` wrapper: invokes ``playwright uninstall chromium``.
No VSA dependency.

Like :mod:`.install`, the ``runpy.run_module`` call goes through
attribute lookup so unit tests can :func:`monkeypatch.setattr` the
``runpy.run_module`` symbol and observe the call.
"""

from __future__ import annotations

import argparse
import logging
import runpy
import sys

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    """Аргументы ``uninstall`` (no extra fields)."""


class Operation(BaseOperation):
    """Удалит Chromium и другие зависимости."""

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace) -> int:
        sys.argv = ["playwright", "uninstall", "chromium"]
        runpy.run_module("playwright", run_name="__main__")
        return 0


__all__ = ("Operation", "Namespace")
