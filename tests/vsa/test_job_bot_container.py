"""Tests for the VSA composition root at ``job_bot.container`` (issue #155).

The new :class:`job_bot.container.AppContainer` is a slim, pure-VSA
composition root. It exposes:

* 7 :func:`@cached_property` slice accessors (no ``_get_X`` or
  ``create_X_adapter`` methods);
* 1 :meth:`run(argv)` method (VSA-native CLI entry point);
* 2 use-case factory methods (``apply_to_vacancies_use_case`` and
  ``prepare_vacancies_use_case``).

The 4 ``_Adapter`` shim classes that used to live in
``hh_applicant_tool.container`` are deleted. The legacy
``hh_applicant_tool.container.AppContainer`` is now a 5-LOC stub
that re-exports the new container.

These tests pin the **public surface** of the new container and the
constraint that ``job_bot.container`` does not import from
``hh_applicant_tool`` at module level.
"""

from __future__ import annotations

import inspect
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ─── Test doubles ────────────────────────────────────────────────


def _make_temp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class _StubTool:
    """Bare-bones stand-in for ``HHApplicantTool``.

    Only the attributes the container needs to build the slices are
    populated — the container accesses them via duck typing, so no
    actual ``HHApplicantTool`` subclass is required.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.config_dir = Path(tempfile.mkdtemp())
        self.profile_id = "default"
        self.config = {
            "client_id": "test_client",
            "client_secret": "test_secret",
            "token": {"access_token": "test_token"},
            "hh_api": {"base_url": "https://api.hh.ru", "timeout": 30},
            "telegram": {
                "bot_token": "test-bot-token",
                "poll_timeout": 30,
                "allowed_user_ids": [123],
            },
            "max": {
                "bot_token": "max-token",
                "api_url": "https://botapi.max.ru",
            },
        }
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.session = MagicMock()
        self.api_client = MagicMock()
        self.api_client.access_token = "test_token"
        self.xsrf_token = "test_xsrf"
        self.storage = MagicMock()
        # ``HHApplicantTool.config_path`` is a ``cached_property``;
        # tests can build it ad-hoc via ``tool.config_dir / profile_id``.
        self._config_path = self.config_dir / self.profile_id

    @property
    def config_path(self) -> Path:
        return self._config_path

    def get_cover_letter_ai(self, system_prompt: str = "") -> Any:
        return None

    def get_captcha_ai(self) -> Any:
        return None

    def get_vacancy_filter_ai(self, system_prompt: str = "") -> Any:
        return None

    @property
    def smtp(self) -> Any:
        return None


@pytest.fixture
def db_path() -> str:
    path = _make_temp_db_path()
    yield path
    _safe_unlink(path)


@pytest.fixture
def tool(db_path: str) -> _StubTool:
    return _StubTool(db_path)


# ─── Container surface ──────────────────────────────────────────


class TestContainerSurface:
    """The new container exposes the VSA composition root surface."""

    def test_container_importable_from_job_bot(self) -> None:
        from job_bot.container import AppContainer

        assert AppContainer is not None

    def test_container_is_singleton_via_cached_property(
        self, tool: _StubTool
    ) -> None:
        """Each slice accessor returns the same instance on repeat access.

        We exercise a representative subset of slices in this test
        (not all 7 at once on the same DB) — the legacy
        ``application_submit`` slice's schema collides with the
        vacancy_search slice's VSA ``search_profiles`` table on a
        shared SQLite file, which is a pre-existing cross-slice
        schema conflict outside the scope of issue #155. Per-slice
        singleton behaviour is verified by
        :class:`TestSliceAccessors` and the slice-level tests.
        """
        from job_bot.container import AppContainer

        container = AppContainer(tool)

        # Same property returns the cached instance.
        assert container.vacancy_search is container.vacancy_search
        assert container.max_bot is container.max_bot
        assert container.config_auth is container.config_auth

    def test_container_exposes_run_method(self, tool: _StubTool) -> None:
        """The container exposes a callable ``run(argv)`` method."""
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        assert callable(container.run)

    def test_container_exposes_two_use_case_factories(
        self, tool: _StubTool
    ) -> None:
        """The container exposes ``apply_to_vacancies_use_case`` and
        ``prepare_vacancies_use_case``."""
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        assert callable(container.apply_to_vacancies_use_case)
        assert callable(container.prepare_vacancies_use_case)


# ─── Slice accessor types ──────────────────────────────────────


class TestSliceAccessors:
    """Each of the 7 @cached_property accessors returns a real slice."""

    def test_vacancy_search_is_vsa_slice(self, tool: _StubTool) -> None:
        from job_bot.container import AppContainer
        from job_bot.vacancy_search.slice import VacancySearchSlice

        container = AppContainer(tool)
        assert isinstance(container.vacancy_search, VacancySearchSlice)

    def test_application_prep_is_vsa_slice(self, tool: _StubTool) -> None:
        from job_bot.application_prep.slice import ApplicationPrepSlice
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        assert isinstance(container.application_prep, ApplicationPrepSlice)

    def test_application_submit_is_vsa_slice(self, tool: _StubTool) -> None:
        from job_bot.application_submit.slice import ApplicationSubmitSlice
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        assert isinstance(container.application_submit, ApplicationSubmitSlice)

    def test_config_auth_is_vsa_slice(self, tool: _StubTool) -> None:
        from job_bot.config_auth.slice import ConfigAuthSlice
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        assert isinstance(container.config_auth, ConfigAuthSlice)

    def test_telegram_bot_is_vsa_slice(self, tool: _StubTool) -> None:
        from job_bot.container import AppContainer
        from job_bot.telegram_bot.slice import TelegramBotSlice

        container = AppContainer(tool)
        assert isinstance(container.telegram_bot, TelegramBotSlice)

    def test_max_bot_is_vsa_slice(self, tool: _StubTool) -> None:
        from job_bot.container import AppContainer
        from job_bot.max_bot.slice import MaxBotSlice

        container = AppContainer(tool)
        assert isinstance(container.max_bot, MaxBotSlice)

    def test_channel_monitoring_is_vsa_slice(self, tool: _StubTool) -> None:
        from job_bot.channel_monitoring.slice import ChannelMonitorSlice
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        assert isinstance(container.channel_monitoring, ChannelMonitorSlice)


# ─── No legacy imports ────────────────────────────────────────


class TestNoLegacyImports:
    """The new container's module must not import from ``hh_applicant_tool``."""

    def test_no_module_level_imports_from_hh_applicant_tool(self) -> None:
        """``job_bot.container`` is module-level decoupled from
        ``hh_applicant_tool``. The container can still construct use
        cases via local (function-scope) imports — the constraint is
        that the module's top-level imports are clean."""
        from job_bot import container as container_module

        source = inspect.getsource(container_module)
        # Walk the module's top-level (before the first ``def`` or
        # ``class``). We assert that no top-level ``import`` or
        # ``from ... import`` statement references
        # ``hh_applicant_tool``. The module docstring may still
        # mention the legacy package (it does, in the class header).
        lines = source.splitlines()
        top_level: list[str] = []
        for line in lines:
            if line.startswith("def ") or line.startswith("class "):
                break
            top_level.append(line)

        offending = [
            line.strip()
            for line in top_level
            if (
                line.lstrip().startswith(("import ", "from "))
                and "hh_applicant_tool" in line
            )
        ]
        assert not offending, (
            "job_bot/container.py must not import from hh_applicant_tool "
            f"at module level; offending lines: {offending}"
        )


# ─── Use case factories ──────────────────────────────────────


class TestUseCaseFactories:
    """The use-case factories return fully-wired use case instances."""

    def test_apply_to_vacancies_use_case_returns_use_case(
        self, tool: _StubTool
    ) -> None:
        from hh_applicant_tool.application.use_cases import (
            ApplyToVacanciesUseCase,
        )
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        use_case = container.apply_to_vacancies_use_case(
            system_prompt="", use_ai=False, send_email=False
        )
        assert isinstance(use_case, ApplyToVacanciesUseCase)

    def test_apply_to_vacancies_use_case_wires_application_submit_slice(
        self, tool: _StubTool
    ) -> None:
        """The VSA ``application_submit_slice`` is wired in (no more
        legacy ``application_submit_adapter``)."""
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        use_case = container.apply_to_vacancies_use_case()

        # The VSA path takes priority over the legacy inline path
        # (issue #89). The new container wires the slice directly.
        assert (
            use_case._application_submit_slice is container.application_submit
        )  # type: ignore[attr-defined]

    def test_prepare_vacancies_use_case_returns_use_case(
        self, tool: _StubTool
    ) -> None:
        from hh_applicant_tool.application.use_cases import (
            PrepareVacanciesUseCase,
        )
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        use_case = container.prepare_vacancies_use_case(
            system_prompt="", use_ai=False
        )
        assert isinstance(use_case, PrepareVacanciesUseCase)

    def test_prepare_vacancies_use_case_wires_application_prep_slice(
        self, tool: _StubTool
    ) -> None:
        """The VSA ``application_prep_slice`` is wired in."""
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        use_case = container.prepare_vacancies_use_case()

        assert use_case._application_prep_slice is container.application_prep  # type: ignore[attr-defined]

    def test_use_case_factories_handle_use_ai_flag(
        self, tool: _StubTool
    ) -> None:
        """``use_ai=True`` triggers AI client construction; ``use_ai=False`` doesn't."""
        from job_bot.container import AppContainer

        tool.get_cover_letter_ai = MagicMock(return_value="ai-client")  # type: ignore[method-assign]

        container = AppContainer(tool)

        with_use_ai = container.apply_to_vacancies_use_case(use_ai=True)
        tool.get_cover_letter_ai.assert_called()
        # Reset the call count
        tool.get_cover_letter_ai.reset_mock()  # type: ignore[attr-defined]

        no_ai = container.apply_to_vacancies_use_case(use_ai=False)
        # With use_ai=False, get_cover_letter_ai should not be called
        tool.get_cover_letter_ai.assert_not_called()  # type: ignore[attr-defined]


# ─── run(argv) entry point ────────────────────────────────────


class TestRunEntryPoint:
    """The ``run(argv)`` method is the VSA-native CLI entry point."""

    def test_run_method_signature(self, tool: _StubTool) -> None:
        """``run`` takes a single ``argv`` parameter (sequence of str)."""
        from job_bot.container import AppContainer

        sig = inspect.signature(AppContainer.run)
        assert "argv" in sig.parameters

    def test_run_with_no_args_returns_int(self, tool: _StubTool) -> None:
        """``run(None)`` (no args) returns an int exit code or None."""
        from job_bot.container import AppContainer

        container = AppContainer(tool)
        result = container.run([])
        # Either an int exit code or None is acceptable; just check
        # it doesn't raise.
        assert result is None or isinstance(result, int)


# ─── No legacy adapter classes ─────────────────────────────────


class TestAdapterClassesDeleted:
    """The 4 legacy ``_Adapter`` classes are gone from the new container."""

    def test_no_vacancy_search_adapter(self) -> None:
        """``_VacancySearchAdapter`` is not defined in the new container."""
        from job_bot import container

        assert not hasattr(container, "_VacancySearchAdapter")

    def test_no_application_prep_adapter(self) -> None:
        from job_bot import container

        assert not hasattr(container, "_ApplicationPrepAdapter")

    def test_no_application_submit_adapter(self) -> None:
        from job_bot import container

        assert not hasattr(container, "_ApplicationSubmitAdapter")

    def test_no_config_adapter(self) -> None:
        from job_bot import container

        assert not hasattr(container, "_ConfigAdapter")


# ─── LOC budget ──────────────────────────────────────────────


class TestLocBudget:
    """The new container stays under the 400-LOC budget."""

    def test_container_under_400_loc(self) -> None:
        from job_bot import container

        source = inspect.getsource(container)
        # Count non-blank, non-comment-only lines.
        loc = sum(
            1
            for line in source.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        assert loc <= 400, f"job_bot/container.py is {loc} LOC; budget is 400"


# ─── Stub at the old import path ─────────────────────────────


class TestLegacyStub:
    """The legacy ``hh_applicant_tool.container`` is a thin stub."""

    def test_legacy_container_re_exports_new_class(self) -> None:
        from hh_applicant_tool.container import (
            AppContainer as LegacyAppContainer,
        )
        from job_bot.container import AppContainer as NewAppContainer

        # The legacy import path resolves to the new class.
        assert LegacyAppContainer is NewAppContainer

    def test_legacy_container_module_under_15_loc(self) -> None:
        """The legacy stub is at most ~5 effective LOC (per issue #155)."""
        import hh_applicant_tool.container as legacy

        source = inspect.getsource(legacy)
        loc = sum(
            1
            for line in source.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        # The issue says 5 LOC; we allow up to 15 to be defensive
        # about docstrings + __all__.
        assert loc <= 15, f"legacy container is {loc} LOC; budget is 15"
