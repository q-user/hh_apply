"""Tests for the cover-letters VSA port (issue #77).

Verifies the deprecation contract:
- The legacy module is a shim, not a reimplementation (no classes defined locally).
- Importing the legacy module / accessing DTOs/constants emits **no** warning.
- Instantiating the class emits a :class:`DeprecationWarning`.
- Class identity is preserved (``type(svc) is cover_letters.CoverLetterService``).
- The VSA path is the new source of truth.
"""

from __future__ import annotations

import importlib
import types
import warnings

import pytest

# The VSA path — source of truth.
from job_bot.application_prep.services.cover_letter_service import (
    DEFAULT_LETTER_TEMPLATE as VSADefaultTemplate,
)
from job_bot.application_prep.services.cover_letter_service import (
    CoverLetterService as VsaCoverLetterService,
)
from job_bot.application_prep.services.cover_letter_service import (
    _parse_ai_letter_response as vsa_parse,
)


# ─── helpers ─────────────────────────────────────────────────


def _legacy_module() -> types.ModuleType:
    return importlib.import_module("hh_applicant_tool.services.cover_letters")


def _classes_defined_in(mod: types.ModuleType) -> set[str]:
    """Return names of classes *defined* in ``mod`` (not imported)."""
    return {
        name
        for name, value in vars(mod).items()
        if isinstance(value, type) and value.__module__ == mod.__name__
    }


# ─── shim, not reimplementation ───────────────────────────────


class TestShimNotReimplementation:
    """The legacy module must **not** define any classes locally."""

    def test_legacy_module_has_no_locally_defined_classes(self) -> None:
        mod = _legacy_module()
        local = _classes_defined_in(mod)
        # The only class defined locally is the shim subclass.
        assert local == {"CoverLetterService"}

    def test_vsa_class_is_not_imported_as_local(self) -> None:
        """The shim's class is a subclass, not the VSA class itself."""
        mod = _legacy_module()
        assert mod.CoverLetterService.__module__ == mod.__name__


# ─── no warning on import / attribute access ─────────────────


class TestImportNoWarning:
    """Importing the legacy module must NOT emit DeprecationWarning."""

    def test_legacy_module_import_emits_no_deprecation_warning(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.import_module("hh_applicant_tool.services.cover_letters")
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations, (
                f"Expected no DeprecationWarning on import, got: {deprecations}"
            )

    def test_legacy_constant_access_emits_no_deprecation_warning(
        self,
    ) -> None:
        mod = _legacy_module()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = mod.DEFAULT_LETTER_TEMPLATE
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations, (
                f"Expected no DeprecationWarning on constant access, "
                f"got: {deprecations}"
            )

    def test_legacy_class_access_emits_no_deprecation_warning(self) -> None:
        mod = _legacy_module()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = mod.CoverLetterService
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations, (
                f"Expected no DeprecationWarning on class access, "
                f"got: {deprecations}"
            )


# ─── warning on instantiation ─────────────────────────────────


class TestInstantiationWarning:
    """Instantiating via the legacy path must emit DeprecationWarning."""

    def test_instantiation_emits_deprecation_warning(self) -> None:
        from hh_applicant_tool.services.cover_letters import CoverLetterService

        with pytest.warns(DeprecationWarning, match="issue #77"):
            svc = CoverLetterService(api_client=None)  # type: ignore[arg-type]
        assert isinstance(svc, VsaCoverLetterService)


# ─── class identity ───────────────────────────────────────────


class TestClassIdentity:
    """Class identity invariants: the shim is a real subclass of the VSA class."""

    def test_shim_is_subclass_of_vsa_class(self) -> None:
        from hh_applicant_tool.services.cover_letters import CoverLetterService

        assert issubclass(CoverLetterService, VsaCoverLetterService)

    def test_shim_class_is_not_vsa_class(self) -> None:
        from hh_applicant_tool.services.cover_letters import CoverLetterService

        assert CoverLetterService is not VsaCoverLetterService

    def test_instance_type_is_shim_class(self) -> None:
        from hh_applicant_tool.services.cover_letters import CoverLetterService

        svc = CoverLetterService(api_client=None)  # type: ignore[arg-type]
        assert type(svc) is CoverLetterService

    def test_instance_isinstance_of_vsa_class(self) -> None:
        from hh_applicant_tool.services.cover_letters import CoverLetterService

        svc = CoverLetterService(api_client=None)  # type: ignore[arg-type]
        assert isinstance(svc, VsaCoverLetterService)


# ─── package root re-exports ─────────────────────────────────


class TestPackageRootReExports:
    """The legacy ``hh_applicant_tool.services`` package root re-exports."""

    def test_package_re_exports_via_shim(self) -> None:
        from hh_applicant_tool.services import CoverLetterService as FromPkg
        from hh_applicant_tool.services import (
            DEFAULT_LETTER_TEMPLATE as FromPkgConst,
        )
        from hh_applicant_tool.services.cover_letters import (
            CoverLetterService as FromShimCls,
        )

        assert FromPkg is FromShimCls
        assert FromPkgConst == VSADefaultTemplate


# ─── VSA path is source of truth ─────────────────────────────


class TestVsaIsSourceOfTruth:
    """The VSA path is the canonical source — the shim forwards to it."""

    def test_legacy_module_delegates_to_vsa_parse(self) -> None:
        from hh_applicant_tool.services.cover_letters import (
            _parse_ai_letter_response,
        )

        result = _parse_ai_letter_response('{"cover_letter": "Hello"}')
        assert result == "Hello"
        # Verify it's the same function as the VSA one
        assert _parse_ai_letter_response is vsa_parse

    def test_vsa_path_has_no_deprecation(self) -> None:
        """The VSA service must NOT emit a deprecation warning on import."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.import_module(
                "job_bot.application_prep.services.cover_letter_service"
            )
            deprecations = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert not deprecations, (
                f"VSA path should not emit deprecation, got: {deprecations}"
            )
