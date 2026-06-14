"""Tests for issue #77: deprecation shim for VacancyTestsService.

Verifies:
1. The VSA class is the source of truth (shim subclasses it, not re-imports it).
2. Importing the shim module does NOT emit a DeprecationWarning.
3. Instantiating the shim class DOES emit a DeprecationWarning.
4. Class identity: ``type(shim) is VacancyTestsService``,
   ``isinstance(shim, VsaClass)``.
5. The VSA path is also importable directly.
6. Constants and module-level functions are re-exported as plain names.
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest


# ─── Shim → VSA class relationship ─────────────────────────────


def test_shim_subclasses_vsa_not_reimport() -> None:
    """The shim must subclass the VSA class, not re-import it identically."""
    from hh_applicant_tool.services.vacancy_tests import (
        VacancyTestsService as ShimCls,
    )
    from job_bot.application_submit.services.vacancy_test_service import (
        VacancyTestsService as VsaCls,
    )

    assert issubclass(ShimCls, VsaCls)
    assert ShimCls is not VsaCls  # distinct classes


def test_vsa_class_importable_directly() -> None:
    """The VSA path must be importable without warnings."""
    from job_bot.application_submit.services.vacancy_test_service import (
        VacancyTestsService,
    )

    assert VacancyTestsService is not None


# ─── No warning on import ──────────────────────────────────────


def test_no_warning_on_import() -> None:
    """Importing the shim module must not emit DeprecationWarning."""
    module_name = "hh_applicant_tool.services.vacancy_tests"
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
    from hh_applicant_tool.services.vacancy_tests import (
        VacancyTestsService as ShimCls,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from unittest.mock import MagicMock

        instance = ShimCls(session=MagicMock(), ai_client=None)
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert len(deprecations) == 1, (
        f"Expected exactly 1 DeprecationWarning on init; got {len(deprecations)}"
    )
    msg = str(deprecations[0].message)
    assert "VacancyTestsService" in msg
    assert "vacancy_test_service" in msg
    assert isinstance(instance, ShimCls)


# ─── Class identity ─────────────────────────────────────────────


def test_class_identity() -> None:
    """``type(instance) is ShimCls`` and ``isinstance(instance, VsaCls)``."""
    from hh_applicant_tool.services.vacancy_tests import (
        VacancyTestsService as ShimCls,
    )
    from job_bot.application_submit.services.vacancy_test_service import (
        VacancyTestsService as VsaCls,
    )
    from unittest.mock import MagicMock

    instance = ShimCls(session=MagicMock(), ai_client=None)
    assert type(instance) is ShimCls
    assert isinstance(instance, VsaCls)
    # But not the other way: the VSA class is NOT a ShimCls
    vsa_instance = VsaCls(session=MagicMock(), ai_client=None)
    assert not isinstance(vsa_instance, ShimCls)


# ─── Constants and functions re-exported ───────────────────────


def test_constants_re_exported() -> None:
    """Constants must be importable from the shim module."""
    from hh_applicant_tool.services.vacancy_tests import (
        REFUSAL_WITH_LINK_TEMPLATE,
        SUBMIT_DELAY_RANGE,
    )
    from job_bot.application_submit.services.vacancy_test_service import (
        REFUSAL_WITH_LINK_TEMPLATE as VsaRefusal,
        SUBMIT_DELAY_RANGE as VsaDelay,
    )

    assert REFUSAL_WITH_LINK_TEMPLATE == VsaRefusal
    assert SUBMIT_DELAY_RANGE == VsaDelay


def test_fetch_function_re_exported() -> None:
    """``fetch_vacancy_tests`` must be importable from the shim."""
    from hh_applicant_tool.services.vacancy_tests import fetch_vacancy_tests
    from job_bot.application_submit.services.vacancy_test_service import (
        fetch_vacancy_tests as VsaFetch,
    )

    assert fetch_vacancy_tests is VsaFetch


# ─── Factory / slice wiring ────────────────────────────────────


def test_slice_uses_vsa_path() -> None:
    """The VSA ``test_handler.py`` must import from the VSA path (no legacy dep)."""
    import ast

    path = "src/job_bot/application_submit/handlers/test_handler.py"
    with open(path) as f:
        tree = ast.parse(f.read())
    # Check that there is NO import from ``hh_applicant_tool.services.vacancy_tests``
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (
                node.module
                and "hh_applicant_tool.services.vacancy_tests" in node.module
            ):
                pytest.fail(
                    f"test_handler.py still imports from legacy path: {node.module}"
                )
            if node.module and "vacancy_test_service" in node.module:
                return  # found the VSA import
    pytest.fail(
        "test_handler.py does not import from the VSA vacancy_test_service path"
    )


# ─── Package re-exports ────────────────────────────────────────


def test_services_package_re_exports() -> None:
    """The ``services/__init__.py`` must export ``VacancyTestsService``."""
    from hh_applicant_tool.services import VacancyTestsService

    assert VacancyTestsService is not None


def test_services_package_re_exports_constants() -> None:
    """Constants must NOT be re-exported from ``services/__init__.py``."""
    import hh_applicant_tool.services

    assert "VacancyTestsService" in hh_applicant_tool.services.__all__
    assert (
        "REFUSAL_WITH_LINK_TEMPLATE" not in hh_applicant_tool.services.__all__
    )
