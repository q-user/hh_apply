"""Tests for ApplicationPrepSlice wiring through AppContainer (VSA migration #54).

Verifies that:
  1. AppContainer can create the new ApplicationPrepSlice (issue #54).
  2. AppContainer can create an adapter that wraps the new slice and
     exposes the legacy ``ApplicationsService``-style interface.
  3. ``PrepareVacanciesUseCase`` receives the adapter via its
     ``application_prep_service_factory`` parameter.
  4. The adapter's ``prepare_one`` actually routes through the new
     slice's ``relevance`` and ``cover_letters`` ports.
  5. The legacy services emit ``DeprecationWarning`` on instantiation.
  6. The per-profile filter AI client is properly injected into the
     new slice's ``RelevanceHandler`` (issue #54 followup — restores
     parity with the legacy ``RelevanceService.ai_client`` setter).
  7. The cover-letter AI client is properly injected into the new
     slice's ``CoverLetterHandler``.
  8. The shared ``analysis_to_dict`` utility is used by both the
     new and legacy code paths.
  9. The shared ``build_filter_ai_client`` utility is the single
     source of truth for the per-profile AI client build flow.
"""

from __future__ import annotations

import os
import tempfile
import warnings
from unittest.mock import MagicMock, patch


def _make_temp_db_path() -> str:
    """Create a temporary file path suitable for ``Database(path)``."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


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


class TestPerProfileAIInjection:
    """Per-profile AI client injection (issue #54 followup)."""

    def _make_mock_tool(self):
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
            tool.storage = MagicMock()
            return tool

    def _make_profile(self, ai_filter_mode="heavy"):
        from hh_applicant_tool.storage.models.search_profile import (
            SearchProfileModel,
        )

        return SearchProfileModel(
            id="p1",
            name="django-senior",
            resume_id="r1",
            enabled=True,
            ai_filter_mode=ai_filter_mode,
            search_params={},
        )

    def test_relevance_handler_has_ai_client_setter(self):
        """``RelevanceHandler`` exposes a public ``ai_client`` setter."""
        from job_bot.application_prep.handlers.relevance_handler import (
            RelevanceHandler,
        )
        from job_bot.shared.storage.database import Database

        db_path = _make_temp_db_path()
        try:
            db = Database(db_path)
            handler = RelevanceHandler(database=db)
            assert handler.ai_client is None  # default

            new_ai = MagicMock()
            handler.ai_client = new_ai
            assert handler.ai_client is new_ai
        finally:
            _safe_unlink(db_path)

    def test_cover_letter_handler_has_ai_client_setter(self):
        """``CoverLetterHandler`` exposes a public ``ai_client`` setter."""
        from job_bot.application_prep.handlers.cover_letter_handler import (
            CoverLetterHandler,
        )
        from job_bot.shared.storage.database import Database

        db_path = _make_temp_db_path()
        try:
            db = Database(db_path)
            handler = CoverLetterHandler(database=db)
            assert handler.ai_client is None  # default

            new_ai = MagicMock()
            handler.ai_client = new_ai
            assert handler.ai_client is new_ai
        finally:
            _safe_unlink(db_path)

    def test_adapter_set_filter_ai_client_propagates_to_handler(self):
        """``_ApplicationPrepAdapter.set_filter_ai_client`` propagates the
        AI client to a REAL slice's ``RelevanceHandler`` (issue #54)."""
        from hh_applicant_tool.container import AppContainer
        from job_bot.application_prep.handlers.relevance_handler import (
            RelevanceHandler,
        )
        from job_bot.shared.storage.database import Database

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        slice_ = container._get_application_prep_slice()

        db_path = _make_temp_db_path()
        try:
            db = Database(db_path)
            real_relevance = RelevanceHandler(database=db, api_client=MagicMock())
            slice_._relevance_handler = real_relevance  # type: ignore[attr-defined]

            container._application_prep_adapter = None
            adapter = container.create_application_prep_service()

            new_ai = MagicMock()
            assert real_relevance.ai_client is None
            adapter.set_filter_ai_client(new_ai)
            assert real_relevance.ai_client is new_ai

            adapter.set_filter_ai_client(None)
            assert real_relevance.ai_client is None
        finally:
            _safe_unlink(db_path)

    def test_adapter_set_cover_letter_ai_client_propagates(self):
        """``_ApplicationPrepAdapter.set_cover_letter_ai_client`` propagates
        the AI client to a REAL slice's ``CoverLetterHandler`` (issue #54)."""
        from hh_applicant_tool.container import AppContainer
        from job_bot.application_prep.handlers.cover_letter_handler import (
            CoverLetterHandler,
        )
        from job_bot.shared.storage.database import Database

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        slice_ = container._get_application_prep_slice()

        db_path = _make_temp_db_path()
        try:
            db = Database(db_path)
            real_cover = CoverLetterHandler(database=db)
            slice_._cover_letter_handler = real_cover  # type: ignore[attr-defined]

            container._application_prep_adapter = None
            adapter = container.create_application_prep_service()

            new_ai = MagicMock()
            assert real_cover.ai_client is None
            adapter.set_cover_letter_ai_client(new_ai)
            assert real_cover.ai_client is new_ai
        finally:
            _safe_unlink(db_path)

    def test_prepare_filter_ai_client_heavy_calls_factory(self):
        """``prepare_filter_ai_client`` with ``ai_filter_mode='heavy'``:
        - calls ``relevance.analyze_resume_heavy``;
        - builds the heavy system prompt;
        - calls the factory with the system prompt;
        - sets the returned AI client on a REAL ``RelevanceHandler``.
        """
        from hh_applicant_tool.container import AppContainer
        from job_bot.application_prep.handlers.relevance_handler import (
            RelevanceHandler,
        )
        from job_bot.shared.storage.database import Database

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        slice_ = container._get_application_prep_slice()

        db_path = _make_temp_db_path()
        try:
            db = Database(db_path)
            api_mock = MagicMock()
            api_mock.get.return_value = {
                "title": "Backend",
                "skills": "5 years of Python, Django, FastAPI",
                "skill_set": ["Python", "Django", "FastAPI"],
                "experience": [
                    {
                        "company": "Acme",
                        "position": "Senior Developer",
                        "start": "2020-01",
                        "end": None,
                        "description": "Built scalable APIs",
                    }
                ],
            }
            real_relevance = RelevanceHandler(
                database=db, api_client=api_mock
            )
            slice_._relevance_handler = real_relevance  # type: ignore[attr-defined]

            container._application_prep_adapter = None
            adapter = container.create_application_prep_service()

            ai_mock = MagicMock()
            factory = MagicMock(return_value=ai_mock)
            profile = self._make_profile(ai_filter_mode="heavy")
            resume = {"id": "r1", "title": "Backend"}

            result = adapter.prepare_filter_ai_client(
                profile, resume, factory
            )

            factory.assert_called_once()
            system_prompt = factory.call_args[0][0]
            assert "Backend" in system_prompt
            assert "Python" in system_prompt
            assert real_relevance.ai_client is ai_mock
            assert result is ai_mock
        finally:
            _safe_unlink(db_path)

    def test_prepare_filter_ai_client_light_calls_factory(self):
        """``prepare_filter_ai_client`` with ``ai_filter_mode='light'``:
        - calls ``relevance.analyze_resume_light``;
        - builds the light system prompt;
        - calls the factory with the system prompt;
        - sets the returned AI client on a REAL ``RelevanceHandler``.
        """
        from hh_applicant_tool.container import AppContainer
        from job_bot.application_prep.handlers.relevance_handler import (
            RelevanceHandler,
        )
        from job_bot.shared.storage.database import Database

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        slice_ = container._get_application_prep_slice()

        db_path = _make_temp_db_path()
        try:
            db = Database(db_path)
            api_mock = MagicMock()
            api_mock.get.return_value = {
                "title": "Backend",
                "skill_set": ["Python", "Django"],
            }
            real_relevance = RelevanceHandler(
                database=db, api_client=api_mock
            )
            slice_._relevance_handler = real_relevance  # type: ignore[attr-defined]

            container._application_prep_adapter = None
            adapter = container.create_application_prep_service()

            ai_mock = MagicMock()
            factory = MagicMock(return_value=ai_mock)
            profile = self._make_profile(ai_filter_mode="light")
            resume = {"id": "r1", "title": "Backend"}

            result = adapter.prepare_filter_ai_client(
                profile, resume, factory
            )

            factory.assert_called_once()
            system_prompt = factory.call_args[0][0]
            assert "Python" in system_prompt
            assert real_relevance.ai_client is ai_mock
            assert result is ai_mock
        finally:
            _safe_unlink(db_path)

    def test_prepare_filter_ai_client_no_mode_returns_none(self):
        """If ``ai_filter_mode`` is ``None`` (or empty), no AI client is
        built and the relevance handler is explicitly cleared (real
        handler assertion)."""
        from hh_applicant_tool.container import AppContainer
        from job_bot.application_prep.handlers.relevance_handler import (
            RelevanceHandler,
        )
        from job_bot.shared.storage.database import Database

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        slice_ = container._get_application_prep_slice()

        db_path = _make_temp_db_path()
        try:
            db = Database(db_path)
            real_relevance = RelevanceHandler(database=db, api_client=MagicMock())
            real_relevance.ai_client = MagicMock()  # pre-seed
            slice_._relevance_handler = real_relevance  # type: ignore[attr-defined]

            container._application_prep_adapter = None
            adapter = container.create_application_prep_service()

            factory = MagicMock()
            profile = self._make_profile(ai_filter_mode=None)
            resume = {"id": "r1", "title": "Backend"}

            result = adapter.prepare_filter_ai_client(profile, resume, factory)

            factory.assert_not_called()
            assert result is None
            assert real_relevance.ai_client is None
        finally:
            _safe_unlink(db_path)

    def test_prepare_filter_ai_client_no_ai_filter_mode_returns_none(self):
        """If ``factory`` is ``None``, no AI client is built and the
        relevance handler is explicitly cleared (real handler).

        Renamed from ``test_prepare_filter_ai_client_no_factory_returns_none``
        per polish item #2 (issue #54).
        """
        from hh_applicant_tool.container import AppContainer
        from job_bot.application_prep.handlers.relevance_handler import (
            RelevanceHandler,
        )
        from job_bot.shared.storage.database import Database

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        slice_ = container._get_application_prep_slice()

        db_path = _make_temp_db_path()
        try:
            db = Database(db_path)
            real_relevance = RelevanceHandler(database=db, api_client=MagicMock())
            real_relevance.ai_client = MagicMock()
            slice_._relevance_handler = real_relevance  # type: ignore[attr-defined]

            container._application_prep_adapter = None
            adapter = container.create_application_prep_service()

            profile = self._make_profile(ai_filter_mode="heavy")
            resume = {"id": "r1", "title": "Backend"}

            result = adapter.prepare_filter_ai_client(
                profile, resume, None
            )

            assert result is None
            assert real_relevance.ai_client is None
        finally:
            _safe_unlink(db_path)

    def test_prepare_filter_ai_client_factory_raises_is_handled(self):
        """If the factory raises, the adapter logs a warning and returns
        ``None`` (with the handler cleared) instead of propagating."""
        from hh_applicant_tool.container import AppContainer
        from job_bot.application_prep.handlers.relevance_handler import (
            RelevanceHandler,
        )
        from job_bot.shared.storage.database import Database

        tool = self._make_mock_tool()
        container = AppContainer(tool)
        slice_ = container._get_application_prep_slice()

        db_path = _make_temp_db_path()
        try:
            db = Database(db_path)
            api_mock = MagicMock()
            api_mock.get.return_value = {
                "title": "Backend",
                "skill_set": ["Python"],
            }
            real_relevance = RelevanceHandler(
                database=db, api_client=api_mock
            )
            real_relevance.ai_client = MagicMock()
            slice_._relevance_handler = real_relevance  # type: ignore[attr-defined]

            container._application_prep_adapter = None
            adapter = container.create_application_prep_service()

            def bad_factory(_prompt: str):
                raise RuntimeError("AI unavailable")

            profile = self._make_profile(ai_filter_mode="heavy")
            resume = {"id": "r1", "title": "Backend"}

            result = adapter.prepare_filter_ai_client(
                profile, resume, bad_factory
            )

            assert result is None
            assert real_relevance.ai_client is None
        finally:
            _safe_unlink(db_path)

    def test_use_case_calls_prepare_filter_ai_client_on_new_path(self):
        """End-to-end: ``PrepareVacanciesUseCase`` invokes the
        ``prepare_filter_ai_client`` on the adapter when the new
        VSA path is active (issue #54 acceptance criterion)."""
        from hh_applicant_tool.application.dto import (
            PrepareVacanciesCommand,
        )
        from hh_applicant_tool.application.use_cases.prepare_vacancies import (
            PrepareVacanciesUseCase,
        )
        from hh_applicant_tool.container import AppContainer
        from hh_applicant_tool.storage.models.search_profile import (
            SearchProfileModel,
        )

        tool = self._make_mock_tool()
        container = AppContainer(tool)

        # Stub the storage so the use case can load profiles + save drafts.
        storage = MagicMock()
        profile = SearchProfileModel(
            id="p1",
            name="p1",
            resume_id="r1",
            enabled=True,
            ai_filter_mode="heavy",
            search_params={},
        )
        storage.search_profiles.get.return_value = profile
        storage.search_profiles.find_enabled.return_value = [profile]
        storage.application_drafts.save = MagicMock()
        tool.storage = storage

        adapter_mock = MagicMock()
        adapter_mock.prepare_filter_ai_client.return_value = MagicMock()
        adapter_mock.set_cover_letter_ai_client = MagicMock()
        adapter_mock.prepare_one.return_value = MagicMock(
            status="prepared",
            relevance_score=None,
            relevance_reason=None,
            has_test=False,
            test_status=None,
            id="d1",
        )
        container._application_prep_adapter = adapter_mock

        container._application_prep_slice = MagicMock()
        factory = MagicMock(return_value=adapter_mock)

        use_case = PrepareVacanciesUseCase(
            api_client=MagicMock(),
            session=MagicMock(),
            storage=storage,
            cover_letter_ai=None,
            vacancy_filter_ai_factory=MagicMock(return_value=MagicMock()),
            application_prep_service_factory=factory,
            vacancy_search_service_factory=MagicMock(
                return_value=MagicMock(
                    search=MagicMock(return_value=iter([]))
                )
            ),
        )

        use_case.api_client.get.return_value = {
            "items": [
                {
                    "id": "r1",
                    "title": "Backend",
                    "status": {"id": "published"},
                }
            ]
        }

        use_case.execute(
            PrepareVacanciesCommand(search_profile="p1")
        )

        factory.assert_called()
        adapter_mock.prepare_filter_ai_client.assert_called()
        args, kwargs = adapter_mock.prepare_filter_ai_client.call_args
        assert args[0] is profile
        assert isinstance(args[1], dict)
        assert args[1]["id"] == "r1"
        assert callable(args[2])

    def test_use_case_legacy_path_uses_shared_helper(self):
        """``PrepareVacanciesUseCase._build_relevance_service`` (legacy
        path) and ``_ApplicationPrepAdapter.prepare_filter_ai_client``
        (VSA path) both delegate to the same
        :func:`job_bot.application_prep.utils.build_filter_ai_client` helper.
        """
        import inspect
        import job_bot.application_prep.utils as utils_mod
        from job_bot.application_prep.utils import build_filter_ai_client

        # The helper is exported at module level (the single source of
        # truth for the per-profile AI filter build flow).
        assert hasattr(utils_mod, "build_filter_ai_client")
        assert utils_mod.build_filter_ai_client is build_filter_ai_client

        # Both call sites reference the same helper. We verify this by
        # reading the *module* source (not the method body, which would
        # miss the module-top import) and asserting that the import is
        # present in both call-site modules.
        from hh_applicant_tool.application.use_cases import prepare_vacancies
        import hh_applicant_tool.container as container_mod

        use_case_src = inspect.getsource(prepare_vacancies)
        assert (
            "from job_bot.application_prep.utils import build_filter_ai_client"
            in use_case_src
        )

        container_src = inspect.getsource(container_mod)
        assert (
            "from job_bot.application_prep.utils import build_filter_ai_client"
            in container_src
        )
        # And the function is invoked (not just imported) in each module.
        assert "build_filter_ai_client(" in use_case_src
        assert "build_filter_ai_client(" in container_src


class TestSharedAnalysisToDictHelper:
    """``job_bot.application_prep.utils.analysis_to_dict`` is the
    single source of truth (issue #54 dedupe)."""

    def test_shared_helper_exported(self):
        from job_bot.application_prep.utils import analysis_to_dict

        assert callable(analysis_to_dict)

    def test_shared_helper_handles_legacy_and_new_results(self):
        from job_bot.application_prep.utils import analysis_to_dict

        # Legacy-style (services/relevance.py): has score alias property
        legacy = MagicMock()
        legacy.suitable = True
        legacy.score = 75
        legacy.reason = "ok"
        legacy.raw_response = "raw"
        out = analysis_to_dict(legacy)
        assert out == {
            "suitable": True,
            "score": 75,
            "reason": "ok",
            "raw_response": "raw",
        }

        # New-style (application_prep/models/relevance.py): uses
        # relevance_score attribute
        new = MagicMock()
        new.suitable = False
        new.score = 80  # backwards-compat property
        new.relevance_score = 80
        new.reason = "no"
        new.raw_response = "raw2"
        out = analysis_to_dict(new)
        assert out["suitable"] is False
        assert out["score"] == 80
        assert out["reason"] == "no"
        assert out["raw_response"] == "raw2"

        # None fields are dropped
        empty = MagicMock()
        empty.suitable = True
        empty.score = None
        empty.reason = None
        empty.raw_response = None
        out = analysis_to_dict(empty)
        assert out == {"suitable": True}


class TestSharedBuildFilterAIHelper:
    """``job_bot.application_prep.utils.build_filter_ai_client`` is the
    single source of truth for the per-profile AI filter build flow."""

    def test_helper_module_level(self):
        import job_bot.application_prep.utils as utils_mod

        assert hasattr(utils_mod, "build_filter_ai_client")
        assert callable(utils_mod.build_filter_ai_client)

    def test_helper_no_mode_clears_handler(self):
        from job_bot.application_prep.utils import build_filter_ai_client

        relevance = MagicMock()
        relevance.ai_client = MagicMock()
        result = build_filter_ai_client(
            MagicMock(ai_filter_mode=None),
            {"id": "r1"},
            relevance,
            MagicMock(),
        )
        assert result is None
        assert relevance.ai_client is None
        relevance.analyze_resume_heavy.assert_not_called()
        relevance.analyze_resume_light.assert_not_called()

    def test_helper_no_factory_clears_handler(self):
        from job_bot.application_prep.utils import build_filter_ai_client

        relevance = MagicMock()
        relevance.ai_client = MagicMock()
        result = build_filter_ai_client(
            MagicMock(ai_filter_mode="heavy"),
            {"id": "r1"},
            relevance,
            None,
        )
        assert result is None
        assert relevance.ai_client is None
        relevance.analyze_resume_heavy.assert_not_called()

    def test_helper_heavy_path(self):
        from job_bot.application_prep.utils import build_filter_ai_client

        relevance = MagicMock()
        relevance.analyze_resume_heavy.return_value = "RES"
        ai_client = MagicMock()
        factory = MagicMock(return_value=ai_client)
        result = build_filter_ai_client(
            MagicMock(ai_filter_mode="heavy", relevance_rules=None),
            {"id": "r1"},
            relevance,
            factory,
        )
        relevance.analyze_resume_heavy.assert_called_once()
        relevance.analyze_resume_light.assert_not_called()
        factory.assert_called_once()
        assert "RES" in factory.call_args[0][0]
        assert result is ai_client
        assert relevance.ai_client is ai_client

    def test_helper_light_path(self):
        from job_bot.application_prep.utils import build_filter_ai_client

        relevance = MagicMock()
        relevance.analyze_resume_light.return_value = "RES-LIGHT"
        ai_client = MagicMock()
        factory = MagicMock(return_value=ai_client)
        result = build_filter_ai_client(
            MagicMock(ai_filter_mode="light", relevance_rules=None),
            {"id": "r1"},
            relevance,
            factory,
        )
        relevance.analyze_resume_light.assert_called_once()
        relevance.analyze_resume_heavy.assert_not_called()
        factory.assert_called_once()
        assert "RES-LIGHT" in factory.call_args[0][0]
        assert result is ai_client
        assert relevance.ai_client is ai_client

    def test_helper_factory_raises(self):
        from job_bot.application_prep.utils import build_filter_ai_client

        relevance = MagicMock()
        relevance.ai_client = MagicMock()

        def bad_factory(_prompt: str):
            raise RuntimeError("AI unavailable")

        result = build_filter_ai_client(
            MagicMock(ai_filter_mode="heavy", relevance_rules=None),
            {"id": "r1"},
            relevance,
            bad_factory,
        )
        assert result is None
        assert relevance.ai_client is None

    def test_helper_rate_limit_assigned(self):
        from job_bot.application_prep.utils import build_filter_ai_client

        relevance = MagicMock()
        relevance.analyze_resume_heavy.return_value = "X"
        ai_client = MagicMock()
        factory = MagicMock(return_value=ai_client)
        result = build_filter_ai_client(
            MagicMock(ai_filter_mode="heavy", relevance_rules=None),
            {"id": "r1"},
            relevance,
            factory,
            rate_limit={"rps": 5},
        )
        assert result is ai_client
        assert ai_client.rate_limit == {"rps": 5}
