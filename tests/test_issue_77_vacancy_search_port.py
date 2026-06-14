"""Tests for the vacancy-search VSA port (issue #77).

Verifies the deprecation contract:
- The legacy module is a shim, not a reimplementation (no classes defined locally).
- Importing the legacy module / accessing DTOs/constants emits **no** warning.
- Instantiating the class emits a :class:`DeprecationWarning`.
- Class identity is preserved (``type(svc) is vacancy_search.VacancySearchService``).
- The VSA path is the new source of truth.
"""

from __future__ import annotations

import importlib
import types
import warnings

import pytest

# The VSA path — source of truth.
from job_bot.vacancy_search.services.vacancy_search_service import (
    VacancySearchService as VsaVacancySearchService,
)
from job_bot.vacancy_search.services.vacancy_search_service import (
    build_search_params as vsa_build_search_params,
)


# ─── helpers ─────────────────────────────────────────────────


def _legacy_module() -> types.ModuleType:
    return importlib.import_module("hh_applicant_tool.services.vacancy_search")


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
        assert local == {"VacancySearchService"}

    def test_vsa_class_is_not_imported_as_local(self) -> None:
        mod = _legacy_module()
        assert mod.VacancySearchService.__module__ == mod.__name__


# ─── no warning on import / attribute access ─────────────────


class TestImportNoWarning:
    def test_legacy_module_import_emits_no_deprecation_warning(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.import_module("hh_applicant_tool.services.vacancy_search")
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
            _ = mod.build_search_params
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations

    def test_legacy_class_access_emits_no_deprecation_warning(self) -> None:
        mod = _legacy_module()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = mod.VacancySearchService
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations


# ─── warning on instantiation ─────────────────────────────────


class TestInstantiationWarning:
    def test_instantiation_emits_deprecation_warning(self) -> None:
        from hh_applicant_tool.services.vacancy_search import (
            VacancySearchService,
        )

        with pytest.warns(DeprecationWarning, match="issue #77"):
            svc = VacancySearchService(
                api_client=None,
                per_page=10,
                total_pages=1,  # type: ignore[arg-type]
            )
        assert isinstance(svc, VsaVacancySearchService)


# ─── class identity ───────────────────────────────────────────


class TestClassIdentity:
    def test_shim_is_subclass_of_vsa_class(self) -> None:
        from hh_applicant_tool.services.vacancy_search import (
            VacancySearchService,
        )

        assert issubclass(VacancySearchService, VsaVacancySearchService)

    def test_shim_class_is_not_vsa_class(self) -> None:
        from hh_applicant_tool.services.vacancy_search import (
            VacancySearchService,
        )

        assert VacancySearchService is not VsaVacancySearchService

    def test_instance_type_is_shim_class(self) -> None:
        from hh_applicant_tool.services.vacancy_search import (
            VacancySearchService,
        )

        svc = VacancySearchService(
            api_client=None,
            per_page=10,
            total_pages=1,  # type: ignore[arg-type]
        )
        assert type(svc) is VacancySearchService

    def test_instance_isinstance_of_vsa_class(self) -> None:
        from hh_applicant_tool.services.vacancy_search import (
            VacancySearchService,
        )

        svc = VacancySearchService(
            api_client=None,
            per_page=10,
            total_pages=1,  # type: ignore[arg-type]
        )
        assert isinstance(svc, VsaVacancySearchService)


# ─── package root re-exports ─────────────────────────────────


class TestPackageRootReExports:
    def test_package_re_exports_via_shim(self) -> None:
        from hh_applicant_tool.services import (
            VacancySearchService as FromPkg,
        )
        from hh_applicant_tool.services import (
            build_search_params as FromPkgFn,
        )
        from hh_applicant_tool.services.vacancy_search import (
            VacancySearchService as FromShimCls,
        )

        assert FromPkg is FromShimCls
        assert FromPkgFn is vsa_build_search_params


# ─── VSA path is source of truth ─────────────────────────────


class TestVsaIsSourceOfTruth:
    def test_vsa_path_has_no_deprecation(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.import_module(
                "job_bot.vacancy_search.services.vacancy_search_service"
            )
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations
