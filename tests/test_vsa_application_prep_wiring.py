"""Tests for ApplicationPrepSlice wiring through AppContainer (VSA migration #54).

Verifies that:
  1. AppContainer can create the new ApplicationPrepSlice (issue #54).
  2. AppContainer can create an adapter that wraps the new slice and
     exposes the legacy ``ApplicationsService``-style interface.
  3. ``PrepareVacanciesUseCase`` receives the adapter via its
     ``application_prep_service_factory`` parameter.
  4. The adapter's ``prepare_one`` actually routes through the new
     slice's ``relevance`` and ``cover_letters`` ports.
  5. The legacy services emit ``DeprecationWarning`` on import.
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch


class TestApplicationPrepSliceWiring:
    """Tests that ApplicationPrepSlice is properly wired into the runtime."""

    def _make_mock_tool(self):
        """Create a mock HHApplicantTool with all required attributes."""
        from hh_applicant_tool.main import HHApplicantTool

        with patch.object(HHApplicantTool, "__init__", lambda self: None):
            tool = HHApplicantTool()
            tool.config = {
                "client_id": "test_client",
                "client_secret": "test_secret",
                "token": {"access_token": "test_token"},
                "hh_api": {
                    "base_url": "https://api.hh.ru",
                    "timeout": 30,
                },
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
            # Override storage property with a mock (the adapter
            # writes through storage.application_drafts.save()).
            tool.storage = MagicMock()
            return tool

    def test_app_container_creates_application_prep_slice(self):
        """AppContainer can create an ApplicationPrepSlice instance."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        slice_ = container._get_application_prep_slice()

        assert slice_ is not None
        assert hasattr(slice_, "relevance")
        assert hasattr(slice_, "cover_letters")
        assert hasattr(slice_, "applications")

    def test_app_container_creates_application_prep_adapter(self):
        """AppContainer can create an adapter wrapping the new slice."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        adapter = container.create_application_prep_service()

        assert adapter is not None
        assert hasattr(adapter, "prepare_one")

    def test_prepare_vacancies_use_case_receives_prep_service_factory(self):
        """PrepareVacanciesUseCase receives the application prep service
        factory (mirrors the ``vacancy_search_service_factory`` wiring from
        issue #53)."""
        from hh_applicant_tool.application.use_cases.prepare_vacancies import (
            PrepareVacanciesUseCase,
        )
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        use_case = container.prepare_vacancies_use_case()

        assert isinstance(use_case, PrepareVacanciesUseCase)
        assert hasattr(
            use_case, "_injected_application_prep_service_factory"
        )
        assert (
            use_case._injected_application_prep_service_factory is not None
        )

    def test_factory_returns_adapter_instance(self):
        """The injected factory returns an adapter instance (not None)."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        use_case = container.prepare_vacancies_use_case()

        adapter = use_case._injected_application_prep_service_factory()
        assert adapter is not None
        assert hasattr(adapter, "prepare_one")

    def test_adapter_prepare_one_delegates_to_new_slice(self):
        """Adapter's ``prepare_one`` invokes the new slice's ``relevance``
        and ``cover_letters`` ports (acceptance criterion for #54)."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()

        # Mock storage.application_drafts.save so it just stores the draft
        # in a local list — we don't care about the real DB.
        saved_drafts: list = []
        tool.storage.application_drafts.save = MagicMock(
            side_effect=lambda draft: saved_drafts.append(draft)
        )

        container = AppContainer(tool)
        slice_ = container._get_application_prep_slice()

        # Replace the slice's relevance + cover_letters with mocks
        # so we can assert the adapter actually calls them.
        relevance_mock = MagicMock()
        relevance_result_mock = MagicMock()
        relevance_result_mock.suitable = True
        relevance_result_mock.score = 85
        relevance_result_mock.reason = "good match"
        relevance_result_mock.raw_response = "raw"
        relevance_mock.is_suitable_heavy.return_value = relevance_result_mock
        relevance_mock.is_suitable_light.return_value = relevance_result_mock
        slice_._relevance_handler = relevance_mock  # type: ignore[attr-defined]

        cover_letter_mock = MagicMock()
        cover_letter_mock.generate_cover_letter.return_value = (
            "Hi, this is a test cover letter."
        )
        slice_._cover_letter_handler = cover_letter_mock  # type: ignore[attr-defined]

        # Re-create the adapter so it picks up the patched handlers
        container._application_prep_adapter = None  # invalidate cache
        adapter = container.create_application_prep_service()

        # Call prepare_one with ai_filter_mode="heavy" so the relevance
        # path is exercised end-to-end.
        draft = adapter.prepare_one(
            resume={"id": "r1", "title": "Backend"},
            vacancy={
                "id": 1,
                "name": "Senior Python",
                "employer": {"id": 42, "name": "Acme"},
                "has_test": False,
                "response_letter_required": True,
            },
            ai_filter_mode="heavy",
            placeholders={"first_name": "Ivan", "last_name": "P"},
            force_message=True,
        )

        # New slice's relevance port was invoked
        relevance_mock.is_suitable_heavy.assert_called_once()
        # New slice's cover_letters port was invoked
        cover_letter_mock.generate_cover_letter.assert_called_once()
        # Draft was saved to legacy storage
        assert tool.storage.application_drafts.save.called
        # And the returned object reflects the AI / cover-letter fields
        assert draft.status == "prepared"
        assert draft.relevance_score == 85
        assert draft.relevance_reason == "good match"
        assert draft.cover_letter == "Hi, this is a test cover letter."

    def test_deprecation_warning_on_instantiating_old_services(self):
        """Instantiating the legacy services emits a DeprecationWarning
        pointing to the new slice (issue #54 acceptance criterion).

        Note: warnings are emitted on instantiation (not at import time)
        so that re-exports through ``services/__init__.py`` don't pollute
        every test run.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # Construct each legacy service; each should emit one
            # DeprecationWarning (the first time it's instantiated in
            # the current process).
            from hh_applicant_tool.services.applications import (
                ApplicationsService,
            )
            from hh_applicant_tool.services.cover_letters import (
                CoverLetterService,
            )
            from hh_applicant_tool.services.relevance import (
                RelevanceService,
            )

            # ``filterwarnings('default')`` already makes
            # DeprecationWarnings show once per (location, category)
            # combination, but we use ``always`` to make sure we catch
            # all of them regardless of pytest's filter settings.
            ApplicationsService(storage=MagicMock())
            CoverLetterService(api_client=MagicMock())
            RelevanceService(api_client=MagicMock())

            deprecation_warnings = [
                warning
                for warning in w
                if issubclass(warning.category, DeprecationWarning)
            ]
            assert deprecation_warnings, (
                "Expected at least one DeprecationWarning when "
                "instantiating the legacy services"
            )

            all_messages = " ".join(
                str(warning.message) for warning in deprecation_warnings
            )
            # All three old service names appear in the captured warnings
            assert "CoverLetterService" in all_messages
            assert "RelevanceService" in all_messages
            assert "ApplicationsService" in all_messages
            # And all point to the new slice
            assert "job_bot.application_prep" in all_messages
