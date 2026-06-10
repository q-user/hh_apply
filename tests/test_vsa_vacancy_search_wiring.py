"""Tests for VacancySearchSlice wiring through AppContainer (VSA migration #53)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch



class TestVacancySearchSliceWiring:
    """Tests that VacancySearchSlice is properly wired into the runtime."""

    def _make_mock_tool(self):
        """Create a mock HHApplicantTool with all required attributes."""
        from hh_applicant_tool.main import HHApplicantTool
        with patch.object(HHApplicantTool, "__init__", lambda self: None):
            tool = HHApplicantTool()
            tool.config = {
                "client_id": "test_client",
                "client_secret": "test_secret",
                "token": {"access_token": "test_token"},
                "hh_api": {"base_url": "https://api.hh.ru", "timeout": 30},
            }
            tool.db_path = "/tmp/test.db"
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

    def test_app_container_creates_vacancy_search_slice(self):
        """AppContainer can create a VacancySearchSlice instance."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()

        container = AppContainer(tool)
        slice = container._get_vacancy_search_slice()

        assert slice is not None
        assert hasattr(slice, "search")

    def test_app_container_creates_vacancy_search_adapter(self):
        """AppContainer can create a vacancy search adapter."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()

        container = AppContainer(tool)
        adapter = container.create_vacancy_search_adapter(
            per_page=10, total_pages=2
        )

        assert adapter is not None
        assert hasattr(adapter, "search")

    def test_apply_to_vacancies_use_case_receives_factory(self):
        """ApplyToVacanciesUseCase receives the vacancy search service factory."""
        from hh_applicant_tool.application.use_cases import (
            ApplyToVacanciesUseCase,
        )
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()

        container = AppContainer(tool)
        use_case = container.apply_to_vacancies_use_case()

        assert isinstance(use_case, ApplyToVacanciesUseCase)
        assert hasattr(use_case, "_injected_vacancy_search_service_factory")
        assert use_case._injected_vacancy_search_service_factory is not None

    def test_prepare_vacancies_use_case_receives_factory(self):
        """PrepareVacanciesUseCase receives the vacancy search service factory."""
        from hh_applicant_tool.application.use_cases import (
            PrepareVacanciesUseCase,
        )
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()

        container = AppContainer(tool)
        use_case = container.prepare_vacancies_use_case()

        assert isinstance(use_case, PrepareVacanciesUseCase)
        assert hasattr(use_case, "_injected_vacancy_search_service_factory")
        assert use_case._injected_vacancy_search_service_factory is not None

    def test_factory_creates_adapter_with_correct_params(self):
        """Factory creates adapter with correct per_page and total_pages."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()

        container = AppContainer(tool)
        factory = (
            lambda per_page,
            total_pages: container.create_vacancy_search_adapter(
                per_page, total_pages
            )
        )

        adapter1 = factory(10, 2)
        adapter2 = factory(50, 5)

        assert adapter1._per_page == 10
        assert adapter1._total_pages == 2
        assert adapter2._per_page == 50
        assert adapter2._total_pages == 5
