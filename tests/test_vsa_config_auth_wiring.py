"""Tests for ConfigAuthSlice wiring through AppContainer (VSA migration #59)."""

from __future__ import annotations

import json
import tempfile
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestConfigAuthSliceWiring:
    """Tests that ConfigAuthSlice is properly wired into the runtime."""

    def _make_mock_tool(self, config_path: str | None = None):
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

    def _create_test_config_file(self) -> Path:
        """Create a temporary config.json file with test data."""
        config_data = {
            "hh": {"client_id": "test_client", "client_secret": "test_secret"},
            "telegram": {"bot_token": "test_bot_token"},
            "ai": {"api_key": "test_ai_key"},
            "max": {},
            "smtp": {},
            "profiles": {},
            "active_profile": None,
        }
        temp_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(config_data, temp_file)
        temp_file.close()
        return Path(temp_file.name)

    def test_app_container_creates_config_auth_slice(self):
        """AppContainer can create a ConfigAuthSlice instance."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()

        container = AppContainer(tool)
        slice = container._get_config_auth_slice()

        assert slice is not None
        assert hasattr(slice, "config")
        assert hasattr(slice, "auth")
        assert hasattr(slice, "users")

    def test_app_container_creates_config_adapter(self):
        """AppContainer can create a config adapter."""
        from hh_applicant_tool.container import AppContainer

        tool = self._make_mock_tool()

        container = AppContainer(tool)
        adapter = container.create_config_adapter()

        assert adapter is not None
        assert hasattr(adapter, "get")
        assert hasattr(adapter, "save")
        assert hasattr(adapter, "load")

    def test_config_adapter_provides_dict_interface(self):
        """Config adapter provides dict-like interface."""
        from hh_applicant_tool.container import AppContainer

        config_path = self._create_test_config_file()
        try:
            tool = self._make_mock_tool(config_path=str(config_path))

            container = AppContainer(tool)
            # Override the slice's config_path to use our test file
            slice = container._get_config_auth_slice()
            slice._config_path = config_path
            adapter = container.create_config_adapter()

            # Test get method
            assert adapter.get("client_id") == "test_client"
            assert adapter.get("client_secret") == "test_secret"
            assert adapter.get("nonexistent", "default") == "default"

            # Test __getitem__
            assert adapter["client_id"] == "test_client"

            # Test __contains__
            assert "client_id" in adapter
            assert "nonexistent" not in adapter

            # Test iteration
            keys = list(adapter)
            assert "client_id" in keys
            assert "client_secret" in keys

            # Test len
            assert len(adapter) > 0

            # Test keys, values, items
            assert "client_id" in adapter.keys()
            assert "test_client" in adapter.values()
            assert ("client_id", "test_client") in adapter.items()
        finally:
            config_path.unlink(missing_ok=True)

    def test_config_adapter_load_save(self):
        """Config adapter load and save methods work."""
        from hh_applicant_tool.container import AppContainer

        config_path = self._create_test_config_file()
        try:
            tool = self._make_mock_tool(config_path=str(config_path))

            container = AppContainer(tool)
            slice = container._get_config_auth_slice()
            slice._config_path = config_path
            adapter = container.create_config_adapter()

            # Test load (should not raise)
            adapter.load()

            # Test save (should not raise)
            adapter.save(test_key="test_value")
        finally:
            config_path.unlink(missing_ok=True)

    def test_deprecation_warning_on_old_config_import(self):
        """Importing old config module emits DeprecationWarning."""
        import importlib

        import hh_applicant_tool.utils.config as config_module

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # Reload the module to trigger the warning again
            importlib.reload(config_module)

            # Check that DeprecationWarning was raised
            assert len(w) >= 1
            assert any(
                issubclass(warning.category, DeprecationWarning)
                for warning in w
            )
            assert any(
                "deprecated" in str(warning.message).lower() for warning in w
            )
            assert any(
                "job_bot.config_auth" in str(warning.message) for warning in w
            )

    def test_config_adapter_nested_key_access(self):
        """Config adapter supports nested key access with dots."""
        from hh_applicant_tool.container import AppContainer

        config_path = self._create_test_config_file()
        try:
            tool = self._make_mock_tool(config_path=str(config_path))

            container = AppContainer(tool)
            slice = container._get_config_auth_slice()
            slice._config_path = config_path
            adapter = container.create_config_adapter()

            # The new format has nested structures like hh.client_id
            # Test that nested access works
            config_dict = adapter._load_config()
            if "hh" in config_dict and isinstance(config_dict["hh"], dict):
                assert adapter.get("hh.client_id") == config_dict["hh"].get(
                    "client_id"
                )
        finally:
            config_path.unlink(missing_ok=True)

    def test_config_adapter_repr(self):
        """Config adapter has a proper repr."""
        from hh_applicant_tool.container import AppContainer

        config_path = self._create_test_config_file()
        try:
            tool = self._make_mock_tool(config_path=str(config_path))

            container = AppContainer(tool)
            slice = container._get_config_auth_slice()
            slice._config_path = config_path
            adapter = container.create_config_adapter()

            repr_str = repr(adapter)
            assert "_ConfigAdapter" in repr_str
        finally:
            config_path.unlink(missing_ok=True)
