"""Tests for issue #77: deprecation shim for ApplyWorkerService.

Verifies:
1. The VSA class is the source of truth (shim subclasses it, not re-imports it).
2. Importing the shim module does NOT emit a DeprecationWarning.
3. Instantiating the shim class DOES emit a DeprecationWarning.
4. Class identity: ``type(shim) is ApplyWorkerService``,
   ``isinstance(shim, VsaClass)``.
5. The VSA path is also importable directly.
6. Exception classes and DTOs are re-exported as plain names.
7. ``make_default_apply_one`` is re-exported from the shim.
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest


# ─── Shim → VSA class relationship ─────────────────────────────


def test_shim_subclasses_vsa_not_reimport() -> None:
    """The shim must subclass the VSA class, not re-import it identically."""
    from hh_applicant_tool.services.apply_worker import (
        ApplyWorkerService as ShimCls,
    )
    from job_bot.application_submit.services.apply_worker_service import (
        ApplyWorkerService as VsaCls,
    )

    assert issubclass(ShimCls, VsaCls)
    assert ShimCls is not VsaCls  # distinct classes


def test_vsa_class_importable_directly() -> None:
    """The VSA path must be importable without warnings."""
    from job_bot.application_submit.services.apply_worker_service import (
        ApplyWorkerService,
    )

    assert ApplyWorkerService is not None


# ─── No warning on import ──────────────────────────────────────


def test_no_warning_on_import() -> None:
    """Importing the shim module must not emit DeprecationWarning."""
    module_name = "hh_applicant_tool.services.apply_worker"
    sys.modules.pop(module_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(module_name)
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert not deprecations, (
        f"Expected no DeprecationWarning on import; got {deprecations}"
    )


# ─── Warning on instantiation ──────────────────────────────────


def test_warning_on_instantiation() -> None:
    """Instantiating the shim class must emit DeprecationWarning exactly once."""
    from hh_applicant_tool.services.apply_worker import (
        ApplyWorkerService as ShimCls,
    )
    from unittest.mock import MagicMock

    from hh_applicant_tool.storage.facade import StorageFacade

    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    storage = StorageFacade(conn)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        instance = ShimCls(storage=storage, apply_one=MagicMock())
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert len(deprecations) == 1, (
        f"Expected exactly 1 DeprecationWarning on init; got {len(deprecations)}"
    )
    msg = str(deprecations[0].message)
    assert "ApplyWorkerService" in msg
    assert "apply_worker_service" in msg
    assert isinstance(instance, ShimCls)


# ─── Class identity ─────────────────────────────────────────────


def test_class_identity() -> None:
    """``type(instance) is ShimCls`` and ``isinstance(instance, VsaCls)``."""
    from hh_applicant_tool.services.apply_worker import (
        ApplyWorkerService as ShimCls,
    )
    from job_bot.application_submit.services.apply_worker_service import (
        ApplyWorkerService as VsaCls,
    )
    from unittest.mock import MagicMock

    from hh_applicant_tool.storage.facade import StorageFacade

    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    storage = StorageFacade(conn)
    instance = ShimCls(storage=storage, apply_one=MagicMock())
    assert type(instance) is ShimCls
    assert isinstance(instance, VsaCls)
    # But not the other way: the VSA class is NOT a ShimCls
    vsa_instance = VsaCls(storage=storage, apply_one=MagicMock())
    assert not isinstance(vsa_instance, ShimCls)


# ─── Exception and DTO re-exports ──────────────────────────────


def test_exceptions_re_exported() -> None:
    """Exception classes must be importable from the shim."""
    from hh_applicant_tool.services.apply_worker import (
        FatalError,
        RetryableError,
    )

    assert issubclass(FatalError, Exception)
    assert issubclass(RetryableError, Exception)


def test_dtos_re_exported() -> None:
    """DTO classes must be importable from the shim."""
    from hh_applicant_tool.services.apply_worker import (
        ApplyOneDraftFn,
        ProcessResult,
        RunStats,
    )

    assert ProcessResult is not None
    assert RunStats is not None
    assert ApplyOneDraftFn is not None


def test_constants_re_exported() -> None:
    """Constants must be importable from the shim."""
    from hh_applicant_tool.services.apply_worker import (
        DEFAULT_MAX_ATTEMPTS,
    )

    assert DEFAULT_MAX_ATTEMPTS == 5


def test_make_default_apply_one_re_exported_from_shim() -> None:
    """``make_default_apply_one`` must be importable from the shim."""
    from hh_applicant_tool.services.apply_worker import (
        make_default_apply_one,
    )

    assert callable(make_default_apply_one)


# ─── Factory / slice wiring ────────────────────────────────────


def test_worker_service_uses_vsa_path() -> None:
    """The VSA ``worker_service.py`` must import errors from the VSA path."""
    import ast

    path = "src/job_bot/application_submit/services/worker_service.py"
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (
                node.module
                and "hh_applicant_tool.services.apply_worker" in node.module
            ):
                pytest.fail(
                    f"worker_service.py still imports from legacy path: {node.module}"
                )
            if node.module and "apply_worker_service" in node.module:
                return  # found the VSA import
    pytest.fail(
        "worker_service.py does not import from the VSA apply_worker_service path"
    )


# ─── Package re-exports ────────────────────────────────────────


def test_services_package_re_exports() -> None:
    """The ``services/__init__.py`` must export ``ApplyWorkerService``."""
    from hh_applicant_tool.services import (
        ApplyWorkerService,
        FatalError,
        ProcessResult,
        RetryableError,
        RunStats,
        make_default_apply_one,
    )

    assert ApplyWorkerService is not None
    assert FatalError is not None
    assert RetryableError is not None
    assert ProcessResult is not None
    assert RunStats is not None
    assert callable(make_default_apply_one)
