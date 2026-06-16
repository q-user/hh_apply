"""CLI-операция ``refresh-token`` (VSA-rewrite issue #147).

If the current access token is expired, the op refreshes it via the
slice's :class:`AuthPort` and saves the new credentials. Otherwise, it
prints a notice and returns ``2`` (no-op).

The slice (with its auth port) is constructor-injected.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _AuthSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def auth(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``refresh-token`` (no extra fields)."""


class Operation(BaseOperation):
    """Обновляет access_token и refresh_token в случае необходимости."""

    __aliases__ = ("refresh",)

    def __init__(self, slice_: _AuthSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("refresh-token requires a slice with auth port")
            return 1
        auth = slice_.auth

        if not auth.is_access_expired():
            print("ℹ️ Токен не истек, обновление не требуется.")
            return 2

        new_creds = auth.refresh_access_token()
        if not auth.save_credentials(new_creds):
            print("⚠️ Токен не был обновлен!")
            return 1
        print("✅ Токен успешно обновлен.")
        return 0


__all__ = ("Operation", "Namespace")
