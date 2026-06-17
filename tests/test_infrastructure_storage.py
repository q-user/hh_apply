"""Regression tests for the storage ``db_path`` guard (issue #78).

History
-------
A ``unittest.mock.MagicMock`` instance once leaked into a real
``Database()`` call. ``Path(mock).parent.mkdir(...)`` coerced the mock
to its class name (``"MagicMock"``) and created a stray
``./MagicMock/`` tree on disk. These tests pin the guard that prevents
that regression.

Scope: the VSA storage path (``src/job_bot/shared/storage/``). The
legacy storage module is cleaned up
under issue #77 and is intentionally not guarded here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, PropertyMock

import pytest

from job_bot.shared.storage.database import (
    Database,
    create_database,
    validate_db_path,
)
from job_bot.shared.storage.facade import create_storage_facade

# A single anchor phrase that BOTH branches of validate_db_path use, so the
# tests stay pinned to a single substring and won't drift apart.
_GUARD_PHRASE = "must be a real Path or str"


# ─────────────────────────────────────────────────────────────────────
# validate_db_path: unit-level
# ─────────────────────────────────────────────────────────────────────


class TestValidateDbPath:
    """Direct unit tests for the module-level guard helper."""

    @pytest.mark.parametrize(
        "value",
        [Path("/tmp/x.db"), ":memory:", "/tmp/x.db"],
        ids=["Path", "memory-str", "str"],
    )
    def test_accepts_real_paths(self, value: object) -> None:
        # Should not raise.
        validate_db_path(value)

    def test_accepts_os_pathlike(self, tmp_path: Path) -> None:
        # os.PathLike protocol is honoured: a custom class with __fspath__ works.
        class _P:
            def __init__(self, p: str) -> None:
                self._p = p

            def __fspath__(self) -> str:
                return self._p

        # Should not raise.
        validate_db_path(_P(str(tmp_path / "x.db")))

    def test_accepts_non_path_pathlike(self, tmp_path: Path) -> None:
        # Exercise the os.PathLike branch (not a pathlib.Path subclass).
        class _OsPathOnly:
            def __init__(self, p: str) -> None:
                self._p = p

            def __fspath__(self) -> str:
                return self._p

        validate_db_path(_OsPathOnly(str(tmp_path / "x.db")))  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "mock_factory",
        [Mock, MagicMock, AsyncMock, PropertyMock],
        ids=["Mock", "MagicMock", "AsyncMock", "PropertyMock"],
    )
    def test_rejects_any_mock_subclass(self, mock_factory: type[Mock]) -> None:
        bad = mock_factory()
        with pytest.raises(TypeError, match="Mock"):
            validate_db_path(bad)

    @pytest.mark.parametrize(
        "value",
        [123, 1.5, None, ["a"], {"a": 1}, object(), b"/tmp/x.db"],
        ids=["int", "float", "None", "list", "dict", "object", "bytes"],
    )
    def test_rejects_arbitrary_types(self, value: object) -> None:
        with pytest.raises(TypeError, match=_GUARD_PHRASE):
            validate_db_path(value)

    def test_error_message_mentions_db_path_and_memory(self) -> None:
        with pytest.raises(TypeError) as excinfo:
            validate_db_path(MagicMock())
        msg = str(excinfo.value)
        assert "db_path" in msg
        assert "Mock" in msg
        assert ":memory:" in msg  # points to the in-memory escape hatch


# ─────────────────────────────────────────────────────────────────────
# Database.__init__: integration
# ─────────────────────────────────────────────────────────────────────


class TestDatabaseRejectsMocks:
    """``Database(MagicMock())`` must fail fast, before any mkdir."""

    def test_init_with_magic_mock_raises(self) -> None:
        with pytest.raises(TypeError, match="Mock"):
            Database(MagicMock())

    def test_init_with_plain_mock_raises(self) -> None:
        with pytest.raises(TypeError, match="Mock"):
            Database(Mock())

    def test_init_with_int_raises(self) -> None:
        with pytest.raises(TypeError, match=_GUARD_PHRASE):
            Database(42)  # type: ignore[arg-type]

    def test_init_does_not_create_filesystem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed construction must leave the filesystem untouched.

        Uses ``monkeypatch.chdir(tmp_path)`` so the assertion is independent
        of pytest's CWD.
        """
        monkeypatch.chdir(tmp_path)
        before = set(tmp_path.iterdir())
        with pytest.raises(TypeError):
            Database(MagicMock())
        after = set(tmp_path.iterdir())
        assert after == before, (
            f"Database(MagicMock()) created stray paths: {after - before}"
        )

    def test_init_with_real_str_still_works(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "x.db"))
        assert db.path == tmp_path / "x.db"
        assert db.path.parent.exists()

    def test_init_with_path_object_still_works(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "x.db")
        assert db.path == tmp_path / "x.db"

    def test_init_with_memory_string_still_works(self) -> None:
        # ":memory:" is a valid sqlite path; the parent (".") is harmless.
        db = Database(":memory:")
        assert db.path == Path(":memory:")

    def test_create_database_factory_rejects_mock(self) -> None:
        with pytest.raises(TypeError, match="Mock"):
            create_database(MagicMock())


# ─────────────────────────────────────────────────────────────────────
# create_storage_facade (job_bot.shared.storage.facade)
# ─────────────────────────────────────────────────────────────────────


class TestCreateStorageFacadeRejectsMocks:
    """``create_storage_facade`` funnels through ``Database.__init__``,
    but the guard must fire *before* the ``Path(...)`` coercion —
    otherwise ``Path(MagicMock())`` succeeds and returns
    ``Path("MagicMock")`` which slips past the in-``Database`` check."""

    def test_create_storage_facade_with_magic_mock_raises(self) -> None:
        with pytest.raises(TypeError, match="Mock"):
            create_storage_facade(MagicMock())

    def test_create_storage_facade_with_real_path_still_works(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "x.db"
        facade = create_storage_facade(path)
        assert facade.database.path == path


# ─────────────────────────────────────────────────────────────────────
# Smoke: the connection from Database.connect() is still functional
# ─────────────────────────────────────────────────────────────────────


def test_database_still_opens_and_executes(tmp_path: Path) -> None:
    """Sanity: the guard did not regress the happy path."""
    db = Database(tmp_path / "ok.db")
    with db.connect() as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        rows = conn.execute("SELECT x FROM t").fetchall()
    assert [r["x"] for r in rows] == [1]


def test_database_memory_connection_works() -> None:
    db = Database(":memory:")
    with db.connect() as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
        rows = conn.execute("SELECT x FROM t").fetchall()
    assert [r["x"] for r in rows] == [42]
