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
from job_bot.container import AppContainer


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


class MockToolMissingConfig:
    """Partial-mock tool that exposes ``db_path`` but lacks ``config``.

    Used to exercise :class:`job_bot.container.AppContainer`'s
    :func:`functools.cached_property` slice accessors — every factory
    reads ``tool.config`` first, so probing a slice on a tool without
    ``config`` raises :class:`AttributeError` about ``config``. The
    legacy-compat stub must surface that error verbatim rather than
    masking it with a generic ``HHApplicantTool has no attribute``
    message (issue #188 P2).
    """

    db_path: str = "/tmp/mocktool-missing-config.db"

    def __init__(self) -> None:
        # Intentionally NO ``config`` attribute — the slice factories
        # must raise AttributeError about ``config`` when invoked.
        pass


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


# ─── Issue #188 P2: descriptor forwarding must not invoke the factory ──


class TestDescriptorForwarding:
    """``__getattr__`` forwards to container descriptors without invoking
    them, and re-raises the underlying error if the descriptor fails.

    The pre-fix code did ``hasattr(container, name)`` which actually
    triggered the ``cached_property`` descriptor and ran the underlying
    factory. If the factory raised (e.g. ``tool.config`` missing),
    ``hasattr`` swallowed the error and the stub raised its own
    generic ``HHApplicantTool has no attribute X`` message, masking
    the real misconfiguration (issue #188 P2). After the fix the stub
    checks the class-level descriptor registry first, then forwards
    the real ``getattr`` and lets the underlying error propagate.
    """

    def test_underlying_config_error_surfaces_for_slice_descriptor(
        self,
    ) -> None:
        """Probing a slice on a partial-mock tool surfaces the real
        ``config`` error, not the generic stub message (issue #188 P2).

        :class:`MockToolMissingConfig` deliberately lacks ``config``.
        The :class:`AppContainer.vacancy_search` ``cached_property``
        factory reads ``tool.config`` first and raises
        :class:`AttributeError`. The stub must re-raise that error
        verbatim so the caller can see the real misconfiguration
        (missing ``config``), not a misleading
        ``HHApplicantTool has no attribute 'vacancy_search'``.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tool = HHApplicantTool(tool=MockToolMissingConfig())
        with pytest.raises(AttributeError) as excinfo:
            _ = tool.vacancy_search  # noqa: B018
        msg = str(excinfo.value)
        # Real, underlying error mentions ``config`` — the missing
        # attribute on the partial mock that broke the factory.
        assert "config" in msg
        # The generic stub message must NOT mask the real error.
        assert "HHApplicantTool has no attribute" not in msg


# ─── Issue #194: inherited descriptors on ``AppContainer`` subclasses ──


class TestInheritedDescriptorLookup:
    """``__getattr__`` must traverse the MRO via ``inspect.getattr_static``.

    PR #191 used ``vars(type(source)).get(name)`` to detect class-level
    descriptors. This is correct for the exact class but does NOT walk
    the MRO: if a future PR subclasses :class:`AppContainer` and the
    subclass doesn't redefine the ``cached_property`` accessor, the
    class-level check returns ``False``, the code falls into the
    ``except AttributeError: continue`` branch, and any inherited
    descriptor whose factory raises ``AttributeError`` is silently
    masked again — the exact bug issue #188 P2 fixed, but re-opened
    for the subclass case (issue #194).
    """

    def test_inherited_cached_property_surfaces_underlying_error(
        self,
    ) -> None:
        """An inherited ``cached_property`` on an ``AppContainer``
        subclass must surface the real factory error (issue #194).

        A bare subclass that does NOT redefine :pyattr:`vacancy_search`
        inherits the descriptor from :class:`AppContainer`. The stub
        must detect the inherited descriptor and forward the real
        ``getattr`` so the underlying ``config`` ``AttributeError``
        propagates verbatim — not be masked by a generic
        ``HHApplicantTool has no attribute 'vacancy_search'`` message.
        """

        class SubclassedContainer(AppContainer):
            """Bare subclass that does NOT redefine any slice descriptor.

            ``vars(SubclassedContainer)`` is empty (no overrides), so
            the pre-fix ``vars(type(source)).get(name)`` check would
            miss the inherited ``vacancy_search`` ``cached_property``.
            """

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tool = HHApplicantTool(tool=MockToolMissingConfig())
        # Inject the subclassed container — the future-PR scenario
        # described in issue #194. The constructor wires an
        # ``AppContainer``; we swap it for a bare subclass so the
        # MRO-vs-``vars`` distinction is the only thing under test.
        tool._container = SubclassedContainer(MockToolMissingConfig())
        with pytest.raises(AttributeError) as excinfo:
            _ = tool.vacancy_search  # noqa: B018
        msg = str(excinfo.value)
        # Real, underlying error mentions ``config`` — the missing
        # attribute on the partial mock that broke the factory.
        assert "config" in msg
        # The generic stub message must NOT mask the real error.
        assert "HHApplicantTool has no attribute" not in msg


# ─── Issue #188 P3: ``db`` lookup is case-insensitive, ``_db`` is not ──


class TestDbNameCaseHandling:
    """The ``db`` accessor matches canonical case-variants of ``db``.

    The pre-fix code compared ``name == "db"`` literally, so
    ``tool.DB`` / ``tool.Db`` silently fell through to the generic
    :class:`AttributeError`. After the fix any case of ``db`` resolves
    through ``db_path``. Unrelated names (e.g. ``_db``) still raise
    the generic error (issue #188 P3).
    """

    def test_db_uppercase_resolves_via_db_path(
        self, real_db_path: Path
    ) -> None:
        """``tool.DB`` (uppercase) resolves ``db`` via ``db_path``.

        Regression for issue #188 P3: case-sensitive matching silently
        broke any caller that asked for ``tool.DB``. After the fix the
        canonical-case path returns the same
        :class:`sqlite3.Connection` as ``tool.db``.
        """
        inner = MockTool()
        inner.db_path = str(real_db_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tool = HHApplicantTool(tool=inner)
        conn = tool.DB  # noqa: B018
        try:
            assert isinstance(conn, sqlite3.Connection)
            # Smoke-test: the connection is open and usable.
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()

    def test_underscore_db_raises_generic_attribute_error(self) -> None:
        """``tool._db`` is NOT a canonical name and raises the generic
        ``HHApplicantTool has no attribute '_db'`` message.

        Only case-variants of ``db`` trigger the ``db_path`` path
        (issue #188 P3). Unrelated names — even obvious near-misses
        like ``_db`` — fall through to the final
        ``raise AttributeError(...)`` so the caller can see exactly
        which attribute was missing.
        """
        tool = _make_tool()
        with pytest.raises(AttributeError) as excinfo:
            _ = tool._db  # noqa: B018
        msg = str(excinfo.value)
        assert "_db" in msg
        assert "HHApplicantTool has no attribute" in msg
