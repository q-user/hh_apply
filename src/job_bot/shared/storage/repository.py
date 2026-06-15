"""Concrete SQLite repository base for the VSA storage layer.

Issue #144: the old ``BaseRepository`` in this module was an
``ABC[Generic[T]]`` with abstract ``create``/``get_by_id``/``update``/
``delete`` methods. Each of the 6 VSA slice repositories re-implemented
them inline. The new :class:`BaseSqliteRepository` is concrete: it
derives SQL from the model's ``__table__`` classvar and converts
dataclass entities to row dicts via :func:`dataclasses.asdict`. The
legacy ``hh_applicant_tool.storage.repositories.base.BaseRepository``
semantics (``find`` / ``count_total`` / ``clear`` / ``save`` /
``save_batch`` / ``_insert`` / ``_row_to_model``) are ported onto the
new concrete base.

Cross-slice consumers should depend on the ``BaseRepository(Protocol)``
in :mod:`job_bot.shared.storage.ports`, not on this concrete class.
The old ``BaseRepository`` name is kept as a back-compat shim re-export
of :class:`BaseSqliteRepository` with a :class:`DeprecationWarning` and
will be removed in 2.0 (issue #156).
"""

from __future__ import annotations

import logging
import sqlite3
import warnings
from collections.abc import Iterator, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any, ClassVar, Mapping

from .database import Database

logger = logging.getLogger(__package__)

DEFAULT_PRIMARY_KEY = "id"


class BaseSqliteRepository:
    """Concrete SQLite repository base for VSA slice repositories.

    Provides:

    - default CRUD derived from the model's ``__table__`` classvar:
      :meth:`create` / :meth:`get_by_id` / :meth:`update` / :meth:`delete`.
    - legacy methods ported from the ``@dataclass`` legacy base:
      :meth:`find` / :meth:`get` / :meth:`count_total` / :meth:`clear` /
      :meth:`save` / :meth:`save_batch` / :meth:`_insert` /
      :meth:`_row_to_model`.
    - VSA connection helpers used by the 5 VSA repos:
      :meth:`_execute` / :meth:`_execute_one` / :meth:`_execute_write`.

    Subclasses set:

    - ``__table__: str``  -- SQL table name.
    - ``model: type``     -- dataclass with an attribute per column.
    - ``pkey: str = "id"`` -- primary key column name (default ``"id"``).

    The class is no longer an ``ABC``; calling the defaults on an
    unconfigured subclass raises :class:`NotImplementedError` instead.
    """

    model: ClassVar[type | None] = None
    pkey: ClassVar[str] = DEFAULT_PRIMARY_KEY
    __table__: ClassVar[str | None] = None
    insert_excludes: ClassVar[tuple[str, ...]] = ("created_at", "updated_at")
    conflict_columns: ClassVar[tuple[str, ...] | None] = None
    update_excludes: ClassVar[tuple[str, ...]] = ("created_at", "updated_at")

    def __init__(self, database: Database) -> None:
        self._db = database

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def database(self) -> Database:
        """Backing :class:`Database` (Protocol-compatible accessor).

        Exposed as a public property so :class:`BaseRepository` Protocol
        in :mod:`job_bot.shared.storage.ports` is satisfied structurally.
        """
        return self._db

    @property
    def db(self) -> Database:
        """Backing :class:`Database` (legacy VSA accessor)."""
        return self._db

    @property
    def table_name(self) -> str:
        """Resolve the SQL table name from classvars.

        Returns ``__table__`` if set, otherwise the model's class name.
        Raises :class:`NotImplementedError` if neither is configured.
        """
        if self.__table__ is not None:
            return self.__table__
        if self.model is not None:
            return self.model.__name__
        raise NotImplementedError(
            f"{type(self).__name__} has no `__table__` or `model` classvar; "
            "set one before using CRUD methods, or override the method."
        )

    def _table_configured(self) -> bool:
        """``True`` when either ``__table__`` or ``model`` is set."""
        return self.__table__ is not None or self.model is not None

    # ------------------------------------------------------------------
    # VSA connection helpers (preserved from the old ABC)
    # ------------------------------------------------------------------

    def _execute(
        self,
        query: str,
        params: tuple[Any, ...] = (),
    ) -> list[sqlite3.Row]:
        """Execute a read query and return all rows."""
        with self._db.connect() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchall()

    def _execute_one(
        self,
        query: str,
        params: tuple[Any, ...] = (),
    ) -> sqlite3.Row | None:
        """Execute a read query and return one row (or ``None``)."""
        with self._db.connect() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            return row  # type: ignore[no-any-return]

    def _execute_write(
        self,
        query: str,
        params: tuple[Any, ...] = (),
    ) -> int:
        """Execute a write query inside its own transaction.

        Commits and returns the affected row count.
        """
        with self._db.connect() as conn:
            cursor = conn.execute(query, params)
            conn.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Entity <-> row conversion (used by the default CRUD)
    # ------------------------------------------------------------------

    def _entity_to_row(self, entity: Any) -> dict[str, Any]:
        """Convert an entity instance to a row dict for INSERT/UPDATE.

        - ``Mapping`` instances are copied as-is.
        - ``@dataclass`` instances are flattened via :func:`asdict`.
        - Plain objects fall back to ``vars(obj)``.

        The new concrete defaults deliberately avoid the legacy
        ``to_db()`` / ``from_db()`` contract so plain dataclasses can
        use the base class without an adapter.
        """
        if isinstance(entity, Mapping):
            return dict(entity)
        if is_dataclass(entity) and not isinstance(entity, type):
            return asdict(entity)
        return dict(vars(entity))

    def _row_to_model(self, *args: Any) -> Any:
        """Convert a row (or a ``(cursor, row)`` pair) to a model.

        Accepts two call shapes for back-compat with the legacy base:

        - ``_row_to_model(row: sqlite3.Row)`` -- used by :meth:`get_by_id`.
        - ``_row_to_model(cursor: sqlite3.Cursor, row: tuple)`` -- used by
          :meth:`find` and matches the legacy signature verbatim.
        """
        if self.model is None:
            raise NotImplementedError(
                f"{type(self).__name__}.model is not set; override "
                "_row_to_model or set the `model` classvar."
            )
        if len(args) == 1:
            data = dict(args[0])
        else:
            cursor, row = args
            data = {
                col[0]: value
                for col, value in zip(cursor.description, row, strict=False)
            }
        return self.model(**data)

    # ------------------------------------------------------------------
    # Default CRUD (concrete -- derive SQL from __table__ / pkey)
    # ------------------------------------------------------------------

    def create(self, entity: Any) -> Any:
        """Insert a new row from ``entity``.

        Derives the column list from the entity's row dict (dataclass
        fields, mapping keys, or instance attributes). Returns ``entity``
        for fluent chaining. Raises :class:`NotImplementedError` if the
        subclass has not configured ``__table__`` or ``model``.
        """
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override create()."
            )
        data = self._entity_to_row(entity)
        columns = list(data.keys())
        if not columns:
            raise ValueError(
                f"Cannot create {type(self).__name__}: entity {entity!r} "
                "has no columns."
            )
        col_sql = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        query = (
            f"INSERT INTO {self.table_name} ({col_sql}) VALUES ({placeholders})"
        )
        self._execute_write(query, tuple(data.values()))
        return entity

    def get_by_id(self, entity_id: Any) -> Any | None:
        """Get a row by primary key; return ``None`` if missing.

        Constructs the model via ``self.model(**row_dict)`` (the legacy
        ``from_db`` is not required). Raises
        :class:`NotImplementedError` if the subclass has not configured
        ``__table__`` or ``model``.
        """
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override get_by_id()."
            )
        row = self._execute_one(
            f"SELECT * FROM {self.table_name} WHERE {self.pkey} = ?",
            (entity_id,),
        )
        if row is None:
            return None
        return self._row_to_model(row)

    def update(self, entity: Any) -> Any:
        """Overwrite an existing row from ``entity``.

        All entity columns except the primary key are written. Returns
        ``entity`` for fluent chaining. Raises :class:`NotImplementedError`
        if the subclass has not configured ``__table__`` or ``model``.
        """
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override update()."
            )
        data = self._entity_to_row(entity)
        data.pop(self.pkey, None)  # never overwrite the primary key
        if not data:
            return entity
        set_clause = ", ".join(f"{col} = ?" for col in data)
        query = (
            f"UPDATE {self.table_name} SET {set_clause} WHERE {self.pkey} = ?"
        )
        params: tuple[Any, ...] = tuple(data.values()) + (
            getattr(entity, self.pkey),
        )
        self._execute_write(query, params)
        return entity

    def delete(self, entity_id: Any) -> bool:
        """Delete a row by primary key.

        Returns ``True`` if a row was removed, ``False`` if no such row
        existed. Raises :class:`NotImplementedError` if the subclass has
        not configured ``__table__`` or ``model``.
        """
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override delete()."
            )
        rowcount = self._execute_write(
            f"DELETE FROM {self.table_name} WHERE {self.pkey} = ?",
            (entity_id,),
        )
        return rowcount > 0

    # ------------------------------------------------------------------
    # Legacy methods (ported from hh_applicant_tool BaseRepository)
    # ------------------------------------------------------------------

    def find(self, **kwargs: Any) -> Iterator[Any]:
        """Yield rows matching the given keyword filters.

        Operator suffix: ``field__lt``, ``field__in`` etc. (see
        ``hh_applicant_tool.storage.repositories.base.BaseRepository.find``
        for the full operator list). Without any kwargs, ``find()``
        yields every row in the table, ordered by ``rowid DESC``.
        """
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override find()."
            )
        operators: dict[str, str] = {
            "lt": "<",
            "le": "<=",
            "gt": ">",
            "ge": ">=",
            "ne": "!=",
            "eq": "=",
            "like": "LIKE",
            "is": "IS",
            "is_not": "IS NOT",
            "in": "IN",
            "not_in": "NOT IN",
        }
        conditions: list[str] = []
        sql_params: dict[str, Any] = {}
        for key, value in kwargs.items():
            try:
                field, op = key.rsplit("__", 1)
            except ValueError:
                field, op = key, "eq"
            if op in ("in", "not_in"):
                values = value if isinstance(value, (list, tuple)) else [value]
                placeholders: list[str] = []
                for i, v in enumerate(values, 1):
                    p_name = f"{field}_{i}"
                    placeholders.append(f":{p_name}")
                    sql_params[p_name] = v
                conditions.append(
                    f"{field} {operators[op]} ({', '.join(placeholders)})"
                )
            else:
                placeholder = f":{field}"
                sql_params[field] = value
                conditions.append(f"{field} {operators[op]} {placeholder}")
        sql = f"SELECT * FROM {self.table_name}"
        if conditions:
            sql += f" WHERE {' AND '.join(conditions)}"
        sql += " ORDER BY rowid DESC;"
        with self._db.connect() as conn:
            cur = conn.execute(sql, sql_params)
            for row in cur.fetchall():
                yield self._row_to_model(cur, row)

    def get(self, pk: Any) -> Any | None:
        """Legacy: get a row by primary key (returns the first match)."""
        return next(self.find(**{self.pkey: pk}), None)

    def count_total(self) -> int:
        """Return the total row count for this repository's table."""
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override count_total()."
            )
        with self._db.connect() as conn:
            cur = conn.execute(f"SELECT count(*) FROM {self.table_name};")
            return cur.fetchone()[0]  # type: ignore[no-any-return]

    def clear(self, commit: bool | None = None) -> None:
        """Delete every row from this repository's table.

        ``commit`` is accepted for signature parity with the legacy base
        but ignored: the VSA connection helpers commit per call already.
        """
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override clear()."
            )
        with self._db.connect() as conn:
            conn.execute(f"DELETE FROM {self.table_name};")
            conn.commit()

    # ``remove`` is the legacy alias for ``delete`` -- keep it available.
    remove = delete
    clean = clear

    def save(
        self,
        obj: Any,
        /,
        **kwargs: Any,
    ) -> None:
        """Insert/update a single entity (upsert by primary key)."""
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override save()."
            )
        data = self._entity_to_row(obj)
        self._insert(data, **kwargs)

    def save_batch(
        self,
        items: list[Any],
        /,
        **kwargs: Any,
    ) -> None:
        """Insert/update a batch of entities (upsert by primary key)."""
        if not items:
            return
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override save_batch()."
            )
        data = [self._entity_to_row(i) for i in items]
        self._insert(data, batch=True, **kwargs)

    def _insert(
        self,
        data: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        /,
        batch: bool = False,
        upsert: bool = True,
        conflict_columns: Sequence[str] | None = None,
        update_excludes: Sequence[str] | None = None,
        commit: bool | None = None,
    ) -> None:
        """Insert (optionally upsert) one row or a batch of rows.

        Mirrors ``hh_applicant_tool.storage.repositories.base.BaseRepository._insert``
        but adapted to the VSA :class:`Database` connection model: each
        call opens one short-lived connection and commits at the end.
        The ``commit`` kwarg is accepted for legacy parity but ignored.
        """
        if not self._table_configured():
            raise NotImplementedError(
                f"{type(self).__name__} has no `__table__` set; "
                "set the classvar or override _insert()."
            )
        if batch and not data:
            return

        if batch:
            if not data:
                return
            first_row: Mapping[str, Any] = data[0]  # type: ignore[index]
        else:
            first_row = data  # type: ignore[assignment]
        raw_columns = list(dict(first_row).keys())
        # (e.g. ``created_at`` / ``updated_at``).
        columns = [c for c in raw_columns if c not in self.insert_excludes]
        cols_set = set(columns)
        if not columns:
            return
        sql = (
            f"INSERT INTO {self.table_name} ({', '.join(columns)})"
            f" VALUES (:{', :'.join(columns)})"
        )

        if upsert:
            if conflict_columns:
                conflict_set = set(conflict_columns) & cols_set
            else:
                conflict_set = {self.pkey} & cols_set
            if conflict_set:
                sql += f" ON CONFLICT({', '.join(conflict_set)})"
                update_set = (
                    cols_set
                    - conflict_set
                    - {self.pkey}
                    - set(
                        update_excludes
                        if update_excludes is not None
                        else self.update_excludes
                    )
                )
                if update_set:
                    update_clause = ", ".join(
                        f"{c} = excluded.{c}" for c in update_set
                    )
                    sql += f" DO UPDATE SET {update_clause}"
                else:
                    sql += " DO NOTHING"
        sql += ";"
        try:
            with self._db.connect() as conn:
                if batch:
                    conn.executemany(sql, data)
                else:
                    conn.execute(sql, data)
                conn.commit()
        except sqlite3.Error:
            logger.warning("SQL ERROR: %s", sql)
            raise


# ----------------------------------------------------------------------
# Back-compat shim (issue #144 / #156)
# ----------------------------------------------------------------------
#
# ``BaseRepository`` was the old ABC name. It is preserved here as a
# lazy attribute so that ``from .repository import BaseRepository``
# still works for one release (issue #156 will delete it). Using
# ``__getattr__`` (instead of an unconditional ``warnings.warn``) means
# that the deprecation notice fires *only* when a caller actually
# touches the deprecated name -- not every time the module is loaded.

_DEPRECATION_MSG = (
    "BaseRepository is deprecated; use BaseSqliteRepository (concrete "
    "class) or BaseRepository from job_bot.shared.storage.ports "
    "(Protocol) instead. The shim will be removed in 2.0 (issue #156)."
)


def __getattr__(name: str) -> Any:  # PEP 562
    if name == "BaseRepository":
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        return BaseSqliteRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
