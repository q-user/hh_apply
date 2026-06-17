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
    """Minimal storage interface for slices (issue #146).

    Implemented by :class:`job_bot.shared.storage.facade.StorageFacade`.
    The Protocol declares all 15 repository properties (13 legacy +
    2 new VSA repos from ``application_prep/repositories/``) so
    cross-slice consumers can rely on the facade's full surface and
    ``mypy --strict`` will flag a missing implementation on any
    future port adapter.

    The 5 VSA repos (``search_profiles``, ``vacancies``,
    ``application_drafts``, ``cover_letters``, ``relevance_analyses``)
    expose :class:`BaseRepository`-compatible APIs (issue #144); the
    10 legacy repos expose the legacy
    :class:`hh_applicant_tool.storage.repositories.base.BaseRepository`
    dataclass API. Both shapes are reachable through this Protocol --
    the annotations are intentionally ``Any`` so a single Protocol
    can stand in for the heterogeneous legacy/VSA surface.
    """

    database: Database
    """Backing :class:`Database` instance."""

    # ─── 5 VSA repos (BaseSqliteRepository subclasses) ────────────

    @property
    def search_profiles(self) -> Any:
        """Search profiles repository."""
        ...

    @property
    def vacancies(self) -> Any:
        """Vacancies repository."""
        ...

    @property
    def application_drafts(self) -> Any:
        """Application drafts repository."""
        ...

    @property
    def cover_letters(self) -> Any:
        """Cover letters repository (issue #146: new VSA repo)."""
        ...

    @property
    def relevance_analyses(self) -> Any:
        """Relevance analyses repository (issue #146: new VSA repo)."""
        ...

    # ─── 10 legacy repos (hh_applicant_tool BaseRepository) ────────

    @property
    def application_test_answers(self) -> Any:
        """Application test answers repository (legacy)."""
        ...

    @property
    def apply_jobs(self) -> Any:
        """Apply-jobs queue repository (legacy)."""
        ...

    @property
    def employer_sites(self) -> Any:
        """Employer sites repository (legacy)."""
        ...

    @property
    def employers(self) -> Any:
        """Employers repository (legacy)."""
        ...

    @property
    def negotiations(self) -> Any:
        """Negotiations repository (legacy)."""
        ...

    @property
    def resumes(self) -> Any:
        """Resumes repository (legacy)."""
        ...

    @property
    def settings(self) -> Any:
        """Settings repository (legacy)."""
        ...

    @property
    def skipped_vacancies(self) -> Any:
        """Skipped vacancies repository (legacy)."""
        ...

    @property
    def telegram_sessions(self) -> Any:
        """Telegram sessions repository (legacy)."""
        ...

    @property
    def vacancy_contacts(self) -> Any:
        """Vacancy contacts repository (legacy)."""
        ...

    @classmethod
    def from_db_path(cls, db_path: str | Path) -> "StoragePort":
        """Factory to create a storage facade from a database path.

        Issue #146: the canonical one-liner
        ``StorageFacade.from_db_path("data.sqlite")``. The Protocol
        declares the classmethod so adapters can satisfy the
        factory contract structurally.
        """
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
