"""Tests for issue #77: deprecation shim for make_default_apply_one.

Since ``make_default_apply_one`` is a **function** (not a class), the
subclass-warn pattern does not apply. The shim simply re-exports the
function from the VSA path with no module-level warning.

Verifies:
1. The function is importable from both the shim and the VSA path.
2. The shim function IS the VSA function (identity, not a wrapper).
3. Importing the shim module does NOT emit a DeprecationWarning.
4. Calling the function works (basic happy-path smoke test).
5. The VSA path is importable directly.
6. The ``services/__init__.py`` re-exports ``make_default_apply_one``.
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest


# ─── Function identity ─────────────────────────────────────────


def test_shim_re_exports_vsa_function() -> None:
    """The shim must re-export the VSA function, not wrap it."""
    from hh_applicant_tool.services.apply_one import (
        make_default_apply_one as ShimFn,
    )
    from job_bot.application_submit.services.apply_one_service import (
        make_default_apply_one as VsaFn,
    )

    assert ShimFn is VsaFn  # same object, not a wrapper


def test_vsa_function_importable_directly() -> None:
    """The VSA path must be importable without warnings."""
    from job_bot.application_submit.services.apply_one_service import (
        make_default_apply_one,
    )

    assert callable(make_default_apply_one)


# ─── No warning on import ──────────────────────────────────────


def test_no_warning_on_import() -> None:
    """Importing the shim module must not emit DeprecationWarning."""
    module_name = "hh_applicant_tool.services.apply_one"
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


# ─── Basic functionality ───────────────────────────────────────


def test_function_is_callable() -> None:
    """The function must be callable and return a callable."""
    from unittest.mock import MagicMock

    from hh_applicant_tool.services.apply_one import (
        make_default_apply_one,
    )

    api_client = MagicMock()
    api_client.post.return_value = {}
    apply_one_fn = make_default_apply_one(api_client)
    assert callable(apply_one_fn)


# ─── Factory / slice wiring ────────────────────────────────────


def test_slice_uses_vsa_path() -> None:
    """The VSA ``apply_one_handler.py`` must import from the VSA path."""
    import ast

    path = "src/job_bot/application_submit/handlers/apply_one_handler.py"
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (
                node.module
                and "hh_applicant_tool.services.apply_one" in node.module
            ):
                pytest.fail(
                    f"apply_one_handler.py still imports from legacy path: {node.module}"
                )
            if node.module and "apply_one_service" in node.module:
                return  # found the VSA import
    pytest.fail(
        "apply_one_handler.py does not import from the VSA apply_one_service path"
    )


# ─── Package re-exports ────────────────────────────────────────


def test_services_package_re_exports() -> None:
    """The ``services/__init__.py`` must export ``make_default_apply_one``."""
    from hh_applicant_tool.services import make_default_apply_one

    assert callable(make_default_apply_one)


def test_apply_worker_package_re_exports() -> None:
    """The ``apply_worker`` shim must also re-export ``make_default_apply_one``."""
    from hh_applicant_tool.services.apply_worker import (
        make_default_apply_one,
    )

    assert callable(make_default_apply_one)
