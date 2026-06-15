"""Storage Port Protocols -- cross-slice storage interface."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, Protocol

from .database import Database


class BaseRepository(Protocol):
    """Structural Protocol for repositories (issue #144).

    Implemented structurally by
    :class:`job_bot.shared.storage.repository.BaseSqliteRepository`.
    Cross-slice consumers should depend on this Protocol, not on the
    concrete class, so the storage layer can be swapped (e.g. for an
    in-memory test fake) without touching the consumer.
    """

    database: Database
    """Backing :class:`Database` (Protocol-compatible accessor)."""

    def get_by_id(self, entity_id: Any) -> Any | None:
        """Return a single entity by primary key (or ``None``)."""
        ...

    def create(self, entity: Any) -> Any:
        """Insert a new entity; return it for fluent chaining."""
        ...

    def update(self, entity: Any) -> Any:
        """Overwrite an existing entity; return it for fluent chaining."""
        ...

    def delete(self, entity_id: Any) -> bool:
        """Delete an entity by primary key; return ``True`` if a row was removed."""
        ...

    def find(self, **kwargs: Any) -> Iterator[Any]:
        """Yield entities matching the given keyword filters."""
        ...

    def count_total(self) -> int:
        """Return the total row count for this repository's table."""
        ...


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
