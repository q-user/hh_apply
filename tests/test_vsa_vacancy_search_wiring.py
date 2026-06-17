"""Tests for VacancySearchSlice wiring through AppContainer (VSA migration #53, slim #155).

Issue #155 moved :class:`AppContainer` to :mod:`job_bot.container` as a
slim, pure-VSA composition root. The 4 ``_Adapter`` shim classes
(``_VacancySearchAdapter`` etc.) are gone. The container now exposes
a ``vacancy_search`` :func:`@cached_property` slice accessor; the
``_VacancySearchAdapter`` legacy search surface is no longer needed
because the use case wires against the VSA slice directly via
``vacancy_search_service_factory`` (a thin closure that returns the
slice's :class:`VacancySearchPort`).
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch


def _make_temp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class TestVacancySearchSliceWiring:
    """Tests that VacancySearchSlice is properly wired into the runtime."""

    def _make_mock_tool(self):
        """Create a mock HHApplicantTool with all required attributes."""
        from job_bot._legacy_compat.main_stub import HHApplicantTool

        with patch.object(HHApplicantTool, "__init__", lambda self: None):
            tool = HHApplicantTool()
            tool.config = {
                "client_id": "test_client",
                "client_secret": "test_secret",
                "token": {"access_token": "test_token"},
                "hh_api": {"base_url": "https://api.hh.ru", "timeout": 30},
            }
            tool.db_path = _make_temp_db_path()
            tool.session = MagicMock()
            tool.api_client = MagicMock()
            tool.api_client.access_token = "test_token"
            tool.get_cover_letter_ai = MagicMock(return_value=None)
            tool.get_captcha_ai = MagicMock(return_value=None)
            tool.get_vacancy_filter_ai = MagicMock(return_value=None)
            tool.xsrf_token = "test_xsrf"
            tool.smtp = None
            # Override storage property with a mock
            tool.storage = MagicMock()
            return tool

    def _safe_close(self, tool: object) -> None:
        db_path = getattr(tool, "db_path", None)
        if db_path:
            _safe_unlink(db_path)

    def test_app_container_creates_vacancy_search_slice(self):
        """AppContainer's ``vacancy_search`` property returns a VSA slice."""
        from job_bot.container import AppContainer
        from job_bot.vacancy_search.slice import VacancySearchSlice

        tool = self._make_mock_tool()
        try:
            container = AppContainer(tool)
            slice_ = container.vacancy_search

            assert isinstance(slice_, VacancySearchSlice)
            assert hasattr(slice_, "search")
        finally:
            self._safe_close(tool)

    def test_apply_to_vacancies_use_case_receives_factory(self):
        """``apply_to_vacancies_use_case`` wires a VSA-backed
        ``vacancy_search_service_factory`` (the slice's search port)."""
        from job_bot.application_submit.services.use_cases import (
            ApplyToVacanciesUseCase,
        )
        from job_bot.container import AppContainer

        tool = self._make_mock_tool()
        try:
            container = AppContainer(tool)
            use_case = container.apply_to_vacancies_use_case()

            assert isinstance(use_case, ApplyToVacanciesUseCase)
            assert hasattr(use_case, "_injected_vacancy_search_service_factory")
            assert use_case._injected_vacancy_search_service_factory is not None
            # Calling the factory must return the VSA search port.
            port = use_case._injected_vacancy_search_service_factory(10, 2)
            assert port is container.vacancy_search.search
        finally:
            self._safe_close(tool)

    def test_prepare_vacancies_use_case_receives_factory(self):
        """``prepare_vacancies_use_case`` wires a VSA-backed
        ``vacancy_search_service_factory``."""
        from job_bot.application_submit.services.use_cases import (
            PrepareVacanciesUseCase,
        )
        from job_bot.container import AppContainer

        tool = self._make_mock_tool()
        try:
            container = AppContainer(tool)
            use_case = container.prepare_vacancies_use_case()

            assert isinstance(use_case, PrepareVacanciesUseCase)
            assert hasattr(use_case, "_injected_vacancy_search_service_factory")
            assert use_case._injected_vacancy_search_service_factory is not None
        finally:
            self._safe_close(tool)

    def test_factory_returns_vacancy_search_port(self):
        """The VSA-backed ``vacancy_search_service_factory`` returns
        the slice's :class:`VacancySearchPort` (issue #155).

        The legacy ``_VacancySearchAdapter`` accepted ``per_page`` and
        ``total_pages`` kwargs; the new VSA port reads the search
        params from the call args directly. The factory must still
        accept the legacy signature (callers depend on it).
        """
        from job_bot.container import AppContainer

        tool = self._make_mock_tool()
        try:
            container = AppContainer(tool)
            factory = container.apply_to_vacancies_use_case(
                use_ai=False, send_email=False
            )._injected_vacancy_search_service_factory

            port_a = factory(10, 2)
            port_b = factory(50, 5)

            # Same port, different per_page/total_pages — the VSA
            # port doesn't need them (it reads ``search_params``).
            assert port_a is port_b
            assert port_a is container.vacancy_search.search
        finally:
            self._safe_close(tool)
