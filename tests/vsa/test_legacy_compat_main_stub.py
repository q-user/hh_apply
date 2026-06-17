"""Unit tests for :class:`HHApplicantTool`'s ``__getattr__`` behaviour (issue #177).

The legacy service-locator stub used to swallow every unknown attribute and
return ``None``. That hid typos and missing mocks: a slice reading
``tool.bogus`` would silently see ``None`` instead of crashing, masking the
real misconfiguration until a much later ``NoneType has no method X`` error.

After issue #177 the stub:

* raises :class:`AttributeError` for any unknown attribute (typos, missing
  mocks) so the failure happens at the exact line that asks for the wrong
  thing;
* resolves ``db`` from ``db_path`` via plain :func:`getattr` (so class-level
  attributes and :func:`cached_property` accessors are picked up too — not
  just instance ``__dict__`` entries);
* keeps the proxying behaviour for attributes the wrapped ``tool`` /
  :class:`AppContainer` already expose.

These tests pin all three behaviours. They are deliberately hermetic: no
filesystem, no network, no real ``AppContainer`` — the only collaborator is a
hand-rolled ``MockTool`` double.
"""

from __future__ import annotations

import sqlite3
import tempfile
import warnings
from pathlib import Path

import pytest

from job_bot._legacy_compat.main_stub import HHApplicantTool


class MockTool:
    """Minimal stand-in for the legacy ``HHApplicantTool``.

    Only exposes a class-level ``db_path`` to exercise the
    ``getattr``-based lookup (the old ``vars(...).get`` path would have
    missed a class-level attribute).
    """

    # Class-level attribute on purpose: the old code used
    # ``vars(source).get("db_path")`` which only sees instance dicts.
    db_path: str = "/tmp/mocktool-class-level.db"

    def __init__(self) -> None:
        self.existing_attr: str = "round-trip-value"

    def method(self) -> str:
        return "method-result"


@pytest.fixture
def real_db_path() -> Path:
    """Yield a real on-disk path the stub can ``sqlite3.connect`` to."""
    fd, raw = tempfile.mkstemp(suffix=".db")
    import os

    os.close(fd)
    yield Path(raw)
    try:
        Path(raw).unlink()
    except OSError:
        pass


def _make_tool() -> HHApplicantTool:
    """Build a fresh ``HHApplicantTool`` (silences the deprecation warning)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return HHApplicantTool()


# ─── Issue #177 bug #1: __getattr__ no longer returns None ────────────


class TestGetattrRaisesAttributeError:
    """Unknown attributes raise :class:`AttributeError` instead of ``None``."""

    def test_bogus_attribute_on_bare_instance_raises(self) -> None:
        """``HHApplicantTool().bogus`` raises ``AttributeError`` (not None)."""
        tool = _make_tool()
        with pytest.raises(AttributeError) as excinfo:
            _ = tool.bogus  # noqa: B018  (intentional attribute probe)
        # The error message should mention the attribute name so the
        # caller can locate the typo at a glance.
        assert "bogus" in str(excinfo.value)

    def test_bogus_attribute_through_wrapped_tool_raises(self) -> None:
        """``HHApplicantTool(tool=MockTool()).bogus`` raises ``AttributeError``.

        Even with a wrapped tool, an attribute the tool does not expose
        must propagate as ``AttributeError`` (not ``None``). This is the
        exact regression from issue #177.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tool = HHApplicantTool(tool=MockTool())
        with pytest.raises(AttributeError) as excinfo:
            _ = tool.bogus  # noqa: B018
        assert "bogus" in str(excinfo.value)

    def test_bare_db_without_db_path_raises_with_actionable_message(
        self,
    ) -> None:
        """``HHApplicantTool().db`` with no tool raises a helpful error.

        The old code returned ``None`` here, which downstream code happily
        forwarded to ``sqlite3.Connection`` methods and crashed far from
        the actual misconfiguration. The new error names ``db_path`` so
        the fix is obvious.
        """
        tool = _make_tool()
        with pytest.raises(AttributeError) as excinfo:
            _ = tool.db  # noqa: B018
        msg = str(excinfo.value)
        assert "db_path" in msg
        assert "db" in msg


# ─── Issue #177 bug #2: db_path is resolved via getattr, not vars(...) ──


class TestDbPathResolution:
    """The ``db`` accessor resolves ``db_path`` through ``getattr``."""

    def test_db_resolves_class_level_db_path(self, real_db_path: Path) -> None:
        """A class-level ``db_path`` on the wrapped tool is honoured.

        This is the regression from issue #177 bug #2: ``vars(source)``
        only sees instance ``__dict__`` entries, so a class-level
        ``db_path`` (or a ``cached_property``) was missed and the stub
        returned ``None``. With ``getattr(source, "db_path", None)`` the
        class-level attribute is picked up correctly.
        """
        MockTool.db_path = str(real_db_path)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                tool = HHApplicantTool(tool=MockTool())
            conn = tool.db  # noqa: B018
            try:
                assert isinstance(conn, sqlite3.Connection)
                # Smoke-test: the connection is open and usable.
                conn.execute("SELECT 1").fetchone()
            finally:
                conn.close()
        finally:
            MockTool.db_path = "/tmp/mocktool-class-level.db"

    def test_db_uses_instance_db_path(self, real_db_path: Path) -> None:
        """An instance-level ``db_path`` still works (the original happy path)."""
        inner = MockTool()
        inner.db_path = str(real_db_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tool = HHApplicantTool(tool=inner)
        conn = tool.db  # noqa: B018
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()


# ─── Proxy behaviour: existing attributes still round-trip ────────────


class TestAttributeProxy:
    """Attributes present on the wrapped tool are proxied unchanged."""

    def test_existing_attr_round_trip(self) -> None:
        """``tool.existing_attr`` returns ``tool.existing_attr`` (same object)."""
        inner = MockTool()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tool = HHApplicantTool(tool=inner)
        assert tool.existing_attr == inner.existing_attr == "round-trip-value"

    def test_method_call_round_trip(self) -> None:
        """Methods on the wrapped tool are callable through the stub."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tool = HHApplicantTool(tool=MockTool())
        assert tool.method() == "method-result"

    def test_proxy_does_not_shadow_explicit_attribute(self) -> None:
        """``__getattr__`` is only called when the normal lookup fails.

        Setting an attribute on the stub itself must be returned directly
        (Python data-model: ``__getattr__`` runs only when ``__getattribute__``
        raised ``AttributeError``). This guards against the proxy silently
        masking local state.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tool = HHApplicantTool(tool=MockTool())
        tool.local = "stub-local-value"  # type: ignore[attr-defined]
        assert tool.local == "stub-local-value"  # type: ignore[attr-defined]
