"""CLI-операция ``logout`` (VSA-rewrite issue #147).

Calls ``DELETE /oauth/token`` via the slice's API client.

The slice is constructor-injected.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _ApiClientSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def api_client(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``logout`` (no extra fields)."""


class Operation(BaseOperation):
    """Выход из профиля."""

    __aliases__ = ("exit",)

    def __init__(self, slice_: _ApiClientSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("logout requires a slice with api_client")
            return 1
        try:
            slice_.api_client.delete("/oauth/token")
        except Exception as ex:  # noqa: BLE001 — match legacy behaviour
            logger.error(f"Ошибка при выходе из профиля: {ex}")
        return 0


__all__ = ("Operation", "Namespace")
