"""Tests for the relevance VSA port (issue #77).

Verifies the deprecation contract:
- The legacy module is a shim, not a reimplementation (no classes defined locally).
- Importing the legacy module / accessing DTOs/constants/functions emits **no** warning.
- Instantiating the class emits a :class:`DeprecationWarning`.
- Class identity is preserved (``type(svc) is relevance.RelevanceService``).
- The VSA path is the new source of truth.
"""

from __future__ import annotations

import importlib
import types
import warnings

import pytest

# The VSA path — source of truth.
from job_bot.application_prep.services.relevance_service import (
    RelevanceService as VsaRelevanceService,
)


# ─── helpers ─────────────────────────────────────────────────


def _legacy_module() -> types.ModuleType:
    return importlib.import_module("hh_applicant_tool.services.relevance")


def _classes_defined_in(mod: types.ModuleType) -> set[str]:
    return {
        name
        for name, value in vars(mod).items()
        if isinstance(value, type) and value.__module__ == mod.__name__
    }


# ─── shim, not reimplementation ───────────────────────────────


class TestShimNotReimplementation:
    def test_legacy_module_has_no_locally_defined_classes(self) -> None:
        mod = _legacy_module()
        local = _classes_defined_in(mod)
        assert local == {"RelevanceService"}

    def test_vsa_class_is_not_imported_as_local(self) -> None:
        mod = _legacy_module()
        assert mod.RelevanceService.__module__ == mod.__name__


# ─── no warning on import / attribute access ─────────────────


class TestImportNoWarning:
    def test_legacy_module_import_emits_no_deprecation_warning(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.import_module("hh_applicant_tool.services.relevance")
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations

    def test_legacy_constant_access_emits_no_deprecation_warning(
        self,
    ) -> None:
        mod = _legacy_module()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = mod.MAX_RETRIES
            _ = mod.SCORE_MIN
            _ = mod.SCORE_MAX
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations

    def test_legacy_dto_access_emits_no_deprecation_warning(self) -> None:
        mod = _legacy_module()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = mod.RelevanceResult
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations

    def test_legacy_function_access_emits_no_deprecation_warning(
        self,
    ) -> None:
        mod = _legacy_module()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = mod.parse_ai_json_response
            _ = mod.build_filter_system_prompt_heavy
            _ = mod.build_filter_system_prompt_light
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations

    def test_legacy_class_access_emits_no_deprecation_warning(self) -> None:
        mod = _legacy_module()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = mod.RelevanceService
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations


# ─── warning on instantiation ─────────────────────────────────


class TestInstantiationWarning:
    def test_instantiation_emits_deprecation_warning(self) -> None:
        from hh_applicant_tool.services.relevance import RelevanceService

        with pytest.warns(DeprecationWarning, match="issue #77"):
            svc = RelevanceService(api_client=None)  # type: ignore[arg-type]
        assert isinstance(svc, VsaRelevanceService)


# ─── class identity ───────────────────────────────────────────


class TestClassIdentity:
    def test_shim_is_subclass_of_vsa_class(self) -> None:
        from hh_applicant_tool.services.relevance import RelevanceService

        assert issubclass(RelevanceService, VsaRelevanceService)

    def test_shim_class_is_not_vsa_class(self) -> None:
        from hh_applicant_tool.services.relevance import RelevanceService

        assert RelevanceService is not VsaRelevanceService

    def test_instance_type_is_shim_class(self) -> None:
        from hh_applicant_tool.services.relevance import RelevanceService

        svc = RelevanceService(api_client=None)  # type: ignore[arg-type]
        assert type(svc) is RelevanceService

    def test_instance_isinstance_of_vsa_class(self) -> None:
        from hh_applicant_tool.services.relevance import RelevanceService

        svc = RelevanceService(api_client=None)  # type: ignore[arg-type]
        assert isinstance(svc, VsaRelevanceService)


# ─── package root re-exports ─────────────────────────────────


class TestPackageRootReExports:
    def test_package_re_exports_class_via_shim(self) -> None:
        from hh_applicant_tool.services import RelevanceService as FromPkg
        from hh_applicant_tool.services.relevance import (
            RelevanceService as FromShimCls,
        )

        assert FromPkg is FromShimCls

    def test_package_re_exports_dto_via_shim(self) -> None:
        from hh_applicant_tool.services import RelevanceResult as FromPkg
        from hh_applicant_tool.services.relevance import (
            RelevanceResult as FromShim,
        )

        assert FromPkg is FromShim

    def test_package_re_exports_functions_via_shim(self) -> None:
        from hh_applicant_tool.services import (
            build_filter_system_prompt_heavy as FromPkg,
        )
        from hh_applicant_tool.services.relevance import (
            build_filter_system_prompt_heavy as FromShim,
        )

        assert FromPkg is FromShim


# ─── VSA path is source of truth ─────────────────────────────


class TestVsaIsSourceOfTruth:
    def test_vsa_path_has_no_deprecation(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.import_module(
                "job_bot.application_prep.services.relevance_service"
            )
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations
