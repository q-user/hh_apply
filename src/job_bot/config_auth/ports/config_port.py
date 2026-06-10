"""Port for config operations - interface for other slices."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from job_bot.config_auth.models.config import AppConfig


class ConfigPort(Protocol):
    """Port for config load/save operations.

    Other slices should depend on this protocol, not on a concrete handler.
    """

    def load(self, path: Path | str, strict: bool = False) -> AppConfig:
        """Load an :class:`AppConfig` from ``path``."""
        ...

    def save(
        self,
        config: AppConfig,
        path: Path | str,
        backup: bool = False,
    ) -> None:
        """Save ``config`` to ``path`` (atomically)."""
        ...
