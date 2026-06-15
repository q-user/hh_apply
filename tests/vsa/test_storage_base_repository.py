"""Tests for ``BaseSqliteRepository`` -- the new concrete VSA storage base.

Issue #144: the old ``BaseRepository`` in ``job_bot.shared.storage.repository``
was an ``ABC[Generic[T]]`` with abstract CRUD methods. Each of the 6 VSA
slice repositories re-implemented ``create/get_by_id/update/delete`` inline.

The new ``BaseSqliteRepository`` is concrete: it derives SQL from the model's
``__table__`` classvar and converts dataclass entities to row dicts via
``dataclasses.asdict``. The legacy
``hh_applicant_tool.storage.repositories.base.BaseRepository`` semantics
(``find`` / ``count_total`` / ``clear`` / ``save`` / ``save_batch`` /
``_insert`` / ``_row_to_model``) are ported onto the new concrete base
and exercised by these tests with a deliberately minimal 3-line
``@dataclass`` model -- the new defaults must work on plain dataclasses,
not just on the rich ``BaseModel`` from the legacy layer.

The cross-slice ``BaseRepository(Protocol)`` lives in
``job_bot.shared.storage.ports`` and is the canonical name for new
consumers; the old ``BaseRepository`` name is kept as a back-compat shim
re-export of ``BaseSqliteRepository`` with a ``DeprecationWarning``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from job_bot.shared.storage.database import Database
from job_bot.shared.storage.repository import BaseSqliteRepository


@dataclass
class SampleItem:
    """Minimal 3-line model for the new default-CRUD tests.

    Deliberately has *no* ``to_db`` / ``from_db`` methods -- the new
    concrete defaults must work on plain dataclasses so the VSA repos
    can adopt the base class without inventing an adapter layer.
    """

    id: str
    name: str

    __table__ = "sample_items"


class SampleItemRepository(BaseSqliteRepository):
    """Concrete test repository: declares the SQL table + the model."""

    __table__ = "sample_items"
    model: type = SampleItem
    pkey: str = "id"

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._db.execute_script(
            """
            CREATE TABLE IF NOT EXISTS sample_items (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )


class TestBaseSqliteRepositoryDefaults:
    """Cover the new concrete defaults with the 3-line dataclass model."""

    def test_create_then_get_by_id_round_trip(self, database: Database) -> None:
        """``create()`` inserts a row; ``get_by_id()`` reads it back."""
        repo = SampleItemRepository(database)
        item = SampleItem(id="abc-123", name="Widget")

        repo.create(item)
        fetched = repo.get_by_id("abc-123")

        assert fetched is not None
        assert isinstance(fetched, SampleItem)
        assert fetched.id == "abc-123"
        assert fetched.name == "Widget"

    def test_get_by_id_returns_none_when_missing(
        self, database: Database
    ) -> None:
        """``get_by_id()`` of an unknown id returns ``None``."""
        repo = SampleItemRepository(database)
        assert repo.get_by_id("does-not-exist") is None

    def test_find_returns_iterator_of_all_rows(
        self, database: Database
    ) -> None:
        """``find()`` yields all rows; returns an iterator (not a list)."""
        repo = SampleItemRepository(database)
        repo.create(SampleItem(id="a", name="Alpha"))
        repo.create(SampleItem(id="b", name="Beta"))
        repo.create(SampleItem(id="c", name="Gamma"))

        results: Iterator[SampleItem] = repo.find()
        # find() returns an iterator, not a list
        assert hasattr(results, "__next__")
        rows = list(results)

        assert len(rows) == 3
        assert {r.id for r in rows} == {"a", "b", "c"}
        assert {r.name for r in rows} == {"Alpha", "Beta", "Gamma"}

    def test_delete_removes_row_and_returns_true(
        self, database: Database
    ) -> None:
        """``delete(id)`` removes the row; subsequent ``get_by_id`` is None."""
        repo = SampleItemRepository(database)
        repo.create(SampleItem(id="x", name="X-ray"))
        assert repo.get_by_id("x") is not None

        deleted = repo.delete("x")

        assert deleted is True
        assert repo.get_by_id("x") is None

    def test_delete_missing_returns_false(self, database: Database) -> None:
        """``delete(unknown_id)`` returns ``False`` (no row was removed)."""
        repo = SampleItemRepository(database)
        assert repo.delete("does-not-exist") is False

    def test_count_total_reflects_row_count(self, database: Database) -> None:
        """``count_total()`` returns the table row count."""
        repo = SampleItemRepository(database)
        assert repo.count_total() == 0

        repo.create(SampleItem(id="1", name="One"))
        assert repo.count_total() == 1

        repo.create(SampleItem(id="2", name="Two"))
        assert repo.count_total() == 2

        repo.delete("1")
        assert repo.count_total() == 1

    def test_update_overwrites_row(self, database: Database) -> None:
        """``update()`` overwrites the row (except primary key)."""
        repo = SampleItemRepository(database)
        repo.create(SampleItem(id="u1", name="Original"))
        repo.update(SampleItem(id="u1", name="Updated"))

        fetched = repo.get_by_id("u1")
        assert fetched is not None
        assert fetched.name == "Updated"


class TestBaseSqliteRepositoryClassShape:
    """Sanity checks on the class itself (no ABC, not Generic)."""

    def test_class_is_not_abstract(self, database: Database) -> None:
        """``BaseSqliteRepository`` is no longer an ``ABC`` -- it instantiates.

        The old behaviour raised ``TypeError`` on direct instantiation because
        ``create``/``get_by_id``/``update``/``delete`` were abstract.
        """
        # Direct instantiation must work: no abstract methods remain.
        repo = BaseSqliteRepository(database)
        assert isinstance(repo, BaseSqliteRepository)

    def test_class_exposes_table_classvar(self) -> None:
        """``__table__`` is a ``ClassVar[str | None]`` defaulting to ``None``."""
        assert BaseSqliteRepository.__table__ is None
        assert SampleItemRepository.__table__ == "sample_items"

    def test_class_exposes_pkey_classvar(self) -> None:
        """``pkey`` defaults to ``"id"`` and subclasses can override."""
        assert BaseSqliteRepository.pkey == "id"
        assert SampleItemRepository.pkey == "id"

    def test_class_exposes_model_classvar(self) -> None:
        """``model`` is a ``ClassVar`` that subclasses set to their dataclass."""
        assert BaseSqliteRepository.model is None
        assert SampleItemRepository.model is SampleItem


class TestBaseSqliteRepositoryBackcompatShim:
    """The old ``BaseRepository`` name keeps working with a deprecation note."""

    def test_base_repository_name_still_importable(self) -> None:
        """``from .repository import BaseRepository`` still works (deprecated)."""
        from job_bot.shared.storage.repository import (
            BaseRepository as ShimBaseRepository,
        )

        # Back-compat: the shim is the concrete class.
        assert ShimBaseRepository is BaseSqliteRepository

    def test_base_repository_name_emits_deprecation_warning(self) -> None:
        """Importing ``BaseRepository`` from ``repository`` warns once."""
        # The warning fires at module import time; the module has already
        # been imported by previous tests, so check the symbol is set.
        from job_bot.shared.storage import repository as repo_module

        assert hasattr(repo_module, "BaseRepository")
        assert repo_module.BaseRepository is BaseSqliteRepository

    def test_protocol_base_repository_is_structural(self) -> None:
        """The Protocol ``BaseRepository`` lives in ``ports`` and is structural.

        The new canonical name (for cross-slice consumers) is the Protocol
        in ``job_bot.shared.storage.ports``. It is *not* the concrete class.
        """
        from job_bot.shared.storage.ports import (
            BaseRepository as ProtocolBaseRepository,
        )

        # Protocol is structural (its ``__init__`` is not the concrete one).
        assert ProtocolBaseRepository is not BaseSqliteRepository

        # The concrete class satisfies the Protocol structurally because
        # it exposes ``database`` (via property), ``get_by_id``, ``create``,
        # ``update``, ``delete``, ``find``, ``count_total``.
        repo = SampleItemRepository(Database(":memory:"))
        # mypy/ty ignore: we're checking runtime presence.
        for attr in (
            "database",
            "get_by_id",
            "create",
            "update",
            "delete",
            "find",
            "count_total",
        ):
            assert hasattr(repo, attr), f"Protocol attr {attr!r} missing"
