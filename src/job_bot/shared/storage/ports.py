"""Storage Port Protocols -- cross-slice storage interface."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol


class StoragePort(Protocol):
    """Minimal storage interface for slices.

    Implemented by job_bot.shared.storage.facade.StorageFacade.
    """

    @property
    def negotiations(self) -> Any:
        """Negotiations repository."""
        ...

    @property
    def skipped_vacancies(self) -> Any:
        """Skipped vacancies repository."""
        ...

    @property
    def application_drafts(self) -> Any:
        """Application drafts repository."""
        ...

    @classmethod
    def create(cls, db_path: str | Path) -> "StoragePort":
        """Factory to create a storage facade from a database path."""
        ...


class EventBusPort(Protocol):
    """Event bus for cross-slice communication."""

    def subscribe(
        self, event_type: str, handler: Callable[..., Any]
    ) -> None: ...

    def unsubscribe(
        self, event_type: str, handler: Callable[..., Any]
    ) -> None: ...

    def publish(self, event: Any) -> None: ...

    def publish_async(self, event: Any) -> None: ...
