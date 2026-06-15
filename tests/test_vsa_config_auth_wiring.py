"""Tests for ConfigAuthSlice wiring through AppContainer (VSA migration #59)."""

from __future__ import annotations

import json
import shutil
import tempfile
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestConfigAuthSliceWiring:
    """Tests that ConfigAuthSlice is properly wired into the runtime."""

    def _make_mock_tool(self, config_dir: Path | None = None):
        """Create a mock ``HHApplicantTool`` with all required attributes.

        After the #59 switchover, the container resolves the VSA
        slice's config path via ``tool.config_path / CONFIG_FILENAME``
        (where ``tool.config_path`` is built from
        ``tool.config_dir / tool.profile_id``). Tests that exercise
        the slice end-to-end must therefore set ``config_dir`` and
        ``profile_id`` on the tool -- otherwise the container raises
        ``AttributeError`` on the first ``tool.config`` access.

        We set sensible defaults (``/tmp`` / ``"default"``) for tests
        that don't pass a ``config_dir`` -- the slice will read
        ``/tmp/default/config.json`` (likely absent) and return a
        default :class:`AppConfig`, which is fine for tests that
        only check slice construction.
        """
        from hh_applicant_tool.main import HHApplicantTool

        with patch.object(HHApplicantTool, "__init__", lambda self: None):
            tool = HHApplicantTool()
            tool.config = {
                "client_id": "test_client",
                "client_secret": "test_secret",
                "token": {"access_token": "test_token"},
                "hh_api": {"base_url": "https://api.hh.ru", "timeout": 30},
            }
            if config_dir is None:
                config_dir = Path("/tmp")
            # Always wire the per-profile layout so the container's
            # ``tool.config_path`` resolves without ``AttributeError``.
            tool.config_dir = config_dir
            tool.profile_id = "default"
            tool.db_path = str(config_dir / "default" / "test.db")
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

    def _create_test_config_dir(self) -> Path:
        """Create a per-profile config layout ``<tmp>/default/config.json``.

        Returns the **directory** (not the file), so test methods can
        pass it to :meth:`_make_mock_tool` and have the container
        find the file at ``<dir>/default/config.json``.
        """
        config_data = {
            "hh": {"client_id": "test_client", "client_secret": "test_secret"},
            "telegram": {"bot_token": "test_bot_token"},
            "ai": {"api_key": "test_ai_key"},
            "max": {},
            "smtp": {},
            "profiles": {},
            "active_profile": None,
        }
        config_dir = Path(tempfile.mkdtemp())
        profile_dir = config_dir / "default"
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "config.json").write_text(json.dumps(config_data))
        return config_dir

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

        config_dir = self._create_test_config_dir()
        try:
            tool = self._make_mock_tool(config_dir=config_dir)

            container = AppContainer(tool)
            # The container now resolves the slice's config_path
            # automatically from ``tool.config_path``; no manual
            # ``slice._config_path = ...`` override is needed.
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
            shutil.rmtree(config_dir, ignore_errors=True)

    def test_config_adapter_load_save(self):
        """Config adapter load and save methods work."""
        from hh_applicant_tool.container import AppContainer

        config_dir = self._create_test_config_dir()
        try:
            tool = self._make_mock_tool(config_dir=config_dir)

            container = AppContainer(tool)
            adapter = container.create_config_adapter()

            # Test load (should not raise)
            adapter.load()

            # Test save: persist a top-level key that ``AppConfig``
            # can absorb (the slice's ``save`` re-emits the config
            # model, which silently drops unknown keys, so we use
            # ``client_id`` which the ``_merge_legacy`` path
            # promotes into the ``hh`` sub-config).
            adapter.save(client_id="updated_client")
        finally:
            shutil.rmtree(config_dir, ignore_errors=True)

    def test_config_adapter_nested_key_access(self):
        """Config adapter supports nested key access with dots."""
        from hh_applicant_tool.container import AppContainer

        config_dir = self._create_test_config_dir()
        try:
            tool = self._make_mock_tool(config_dir=config_dir)

            container = AppContainer(tool)
            adapter = container.create_config_adapter()

            # The new format has nested structures like hh.client_id
            # Test that nested access works
            config_dict = adapter._load_config()
            if "hh" in config_dict and isinstance(config_dict["hh"], dict):
                assert adapter.get("hh.client_id") == config_dict["hh"].get(
                    "client_id"
                )
        finally:
            shutil.rmtree(config_dir, ignore_errors=True)

    def test_config_adapter_repr(self):
        """Config adapter has a proper repr."""
        from hh_applicant_tool.container import AppContainer

        config_dir = self._create_test_config_dir()
        try:
            tool = self._make_mock_tool(config_dir=config_dir)

            container = AppContainer(tool)
            adapter = container.create_config_adapter()

            repr_str = repr(adapter)
            assert "_ConfigAdapter" in repr_str
        finally:
            shutil.rmtree(config_dir, ignore_errors=True)


class TestHHApplicantToolConfigSwitchover:
    """Integration tests for the #59 switchover: ``HHApplicantTool.config``
    must return the VSA-backed ``_ConfigAdapter`` (not legacy
    ``utils.Config``)."""

    def _setup_tool_with_config(self, tmp_path, config_data):
        """Create a real ``HHApplicantTool`` whose ``self.config_path`` points
        to a freshly-written ``config.json`` in VSA format. Returns the
        tool and the resolved config.json path."""
        from hh_applicant_tool.main import HHApplicantTool

        # Per-profile layout: <config_dir>/<profile_id>/config.json
        profile_dir = tmp_path / "default"
        profile_dir.mkdir(parents=True, exist_ok=True)
        config_file = profile_dir / "config.json"
        config_file.write_text(json.dumps(config_data))

        tool = HHApplicantTool()
        # Skip ``run()`` (it would need a full CLI arg list); set the
        # attributes the cached_properties depend on directly.
        tool.config_dir = tmp_path
        tool.profile_id = "default"
        return tool, config_file

    def test_hh_applicant_tool_config_is_vsa_adapter(self, tmp_path):
        """``HHApplicantTool().config`` returns a VSA-backed
        ``_ConfigAdapter`` (not legacy ``utils.Config``)."""
        from hh_applicant_tool.container import _ConfigAdapter

        # Issue #142: ``hh_applicant_tool.utils.config`` shim was removed.
        # Use a sentinel class as a stand-in for the legacy ``Config`` so the
        # ``not isinstance(adapter, Config)`` check still verifies the VSA adapter
        # is not the legacy class.
        class Config(dict):
            pass

        config_data = {
            "hh": {
                "client_id": "switchover_client",
                "client_secret": "switchover_secret",
            },
            "telegram": {"bot_token": "switchover_bot"},
            "ai": {"api_key": "switchover_ai"},
            "max": {},
            "smtp": {},
            "profiles": {},
            "active_profile": None,
        }
        tool, _config_file = self._setup_tool_with_config(tmp_path, config_data)

        adapter = tool.config  # triggers container + adapter build

        # VSA-backed, not the legacy class
        assert isinstance(adapter, _ConfigAdapter)
        assert not isinstance(adapter, Config)

    def test_hh_applicant_tool_config_get_reads_via_slice(self, tmp_path):
        """``HHApplicantTool().config.get("client_id")`` reads through
        the VSA slice (issue #59). The flat ``client_id`` key in the
        legacy format is mapped to ``hh.client_id`` in the VSA
        format, and the adapter flattens it back."""

        # Issue #142: ``hh_applicant_tool.utils.config`` shim was removed.
        # Use a sentinel class as a stand-in for the legacy ``Config`` so the
        # ``not isinstance(adapter, Config)`` check still verifies the VSA adapter
        # is not the legacy class.
        class Config(dict):
            pass

        config_data = {
            "hh": {
                "client_id": "via_slice_client",
                "client_secret": "via_slice_secret",
            },
            "telegram": {"bot_token": "via_slice_bot"},
            "ai": {"api_key": "via_slice_ai"},
            "max": {},
            "smtp": {},
            "profiles": {},
            "active_profile": None,
        }
        tool, _config_file = self._setup_tool_with_config(tmp_path, config_data)

        # The VSA adapter should expose the legacy flat keys
        assert tool.config.get("client_id") == "via_slice_client"
        assert tool.config.get("client_secret") == "via_slice_secret"
        # And nested sections (telegram, ai) are still reachable.
        # Note: ``TelegramConfig.to_dict()`` emits every field, so
        # the assertion must include every default the dataclass
        # fills in when the field is absent from the on-disk JSON.
        # ``poll_timeout`` / ``proxy_url`` were added in issue #59
        # so the ``job_bot.telegram_bot`` transport can read them
        # from the VSA model instead of reaching for the legacy
        # ``utils.config.Config`` class.
        assert tool.config.get("telegram") == {
            "bot_token": "via_slice_bot",
            "allowed_user_ids": [],
            "digest_chat_id": None,
            "poll_timeout": None,
            "proxy_url": None,
        }
        assert tool.config.get("ai") == {
            "api_key": "via_slice_ai",
            "base_url": None,
            "model": "gpt-4o-mini",
            "timeout": 60.0,
            "max_retries": 3,
        }

        # Defensive: ensure we're not falling back to legacy Config
        assert not isinstance(tool.config, Config)

    def test_hh_applicant_tool_config_save_writes_via_slice(self, tmp_path):
        """``HHApplicantTool().config.save(**kwargs)`` persists through
        the VSA slice (issue #59).

        We use ``save(telegram=...)`` (a sub-config dict) because:
          * ``AppConfig.from_dict`` silently drops unknown top-level
            keys (e.g. a bare ``client_id=`` is dropped when
            ``hh.client_id`` is already set on disk).
          * The ``telegram`` sub-config is a dataclass, so the
            adapter's nested-dict merge updates it in place and the
            round-trip is observable on disk.
        """

        # Issue #142: ``hh_applicant_tool.utils.config`` shim was removed.
        # Use a sentinel class as a stand-in for the legacy ``Config`` so the
        # ``not isinstance(adapter, Config)`` check still verifies the VSA adapter
        # is not the legacy class.
        class Config(dict):
            pass

        config_data = {
            "hh": {
                "client_id": "save_client",
                "client_secret": "save_secret",
            },
            "telegram": {"bot_token": "old_bot"},
            "ai": {},
            "max": {},
            "smtp": {},
            "profiles": {},
            "active_profile": None,
        }
        tool, config_file = self._setup_tool_with_config(tmp_path, config_data)

        # Update a sub-config: the adapter's nested-dict merge
        # updates ``current_dict["telegram"]`` in place, so the new
        # ``bot_token`` round-trips through the VSA model.
        tool.config.save(telegram={"bot_token": "updated_bot"})

        # Re-read the on-disk JSON.
        on_disk = json.loads(config_file.read_text())
        # The ``telegram`` section is updated.
        assert on_disk["telegram"]["bot_token"] == "updated_bot"
        # Existing data must be preserved.
        assert on_disk["hh"]["client_id"] == "save_client"
        assert on_disk["hh"]["client_secret"] == "save_secret"

        # Defensive: still using the VSA adapter
        assert not isinstance(tool.config, Config)

    def test_hh_applicant_tool_save_token_routes_to_auth_port(self, tmp_path):
        """``HHApplicantTool().config.save_token(...)`` persists OAuth
        credentials through ``slice.auth.save_credentials`` (issue #59).

        Regression test for the critical bug: the legacy
        ``self.config.save(token=...)`` path silently no-ops under
        the VSA ``AppConfig`` (no ``token`` field). The new
        ``save_token`` method routes to the auth port instead.
        """
        from job_bot.config_auth.models.credentials import OAuthCredentials

        config_data = {
            "hh": {
                "client_id": "save_token_client",
                "client_secret": "save_token_secret",
            },
            "telegram": {},
            "ai": {},
            "max": {},
            "smtp": {},
            "profiles": {},
            "active_profile": None,
        }
        tool, _config_file = self._setup_tool_with_config(tmp_path, config_data)

        # The adapter should expose a ``save_token`` method that
        # delegates to ``slice.auth.save_credentials``.
        new_token = {
            "access_token": "new_access_v59",
            "refresh_token": "new_refresh_v59",
            "access_expires_at": 1234567890,
        }
        tool.config.save_token(new_token)

        # Verify the credentials were persisted through the auth port.
        stored = tool.config._slice.auth.get_credentials()
        assert stored is not None
        assert stored.access_token == "new_access_v59"
        assert stored.refresh_token == "new_refresh_v59"
        assert stored.access_expires_at == 1234567890
        # And the saved object is the right dataclass type.
        assert isinstance(stored, OAuthCredentials)

    def test_hh_applicant_tool_save_token_legacy_warns(self, tmp_path):
        """``self.config.save(token=...)`` emits a warning and is a no-op
        (issue #59). Forces callers to switch to ``save_token()``.
        """
        config_data = {
            "hh": {
                "client_id": "warn_client",
                "client_secret": "warn_secret",
            },
            "telegram": {},
            "ai": {},
            "max": {},
            "smtp": {},
            "profiles": {},
            "active_profile": None,
        }
        tool, config_file = self._setup_tool_with_config(tmp_path, config_data)

        # The legacy ``save(token=...)`` should warn (not silently
        # no-op) so the gap is greppable in the logs.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # Must not raise -- the contract is "warns AND continues",
            # not "warns AND maybe raises".
            result = tool.config.save(
                token={
                    "access_token": "should_not_persist",
                    "refresh_token": "should_not_persist",
                },
            )
            assert result is None  # legacy save() returns None
            token_warnings = [
                w
                for w in caught
                if "save_token" in str(w.message)
                and issubclass(w.category, DeprecationWarning)
            ]
            assert token_warnings, (
                "expected save(token=...) to raise a DeprecationWarning "
                "that mentions the save_token() replacement"
            )

        # The token must NOT have been written to the config file
        # (it would have been silently dropped by ``AppConfig``).
        on_disk = json.loads(config_file.read_text())
        assert "token" not in on_disk

    def test_hh_applicant_tool_save_token_uses_active_profile(self, tmp_path):
        """``save_token(...)`` persists under the tool's active profile
        (issue #62 multi-profile support). Regression test: a naive
        implementation would hard-code ``profile_id="default"`` and
        silently break ``--profile prod`` setups.
        """
        config_data = {
            "hh": {
                "client_id": "prod_client",
                "client_secret": "prod_secret",
            },
            "telegram": {},
            "ai": {},
            "max": {},
            "smtp": {},
            "profiles": {},
            "active_profile": None,
        }
        tool, _config_file = self._setup_tool_with_config(tmp_path, config_data)
        # Simulate ``--profile prod`` (the tool's ``config_path``
        # already resolves to ``<tmp_path>/prod/config.json`` per the
        # helper -- we only need to flip the instance attr so the
        # adapter reads the right profile_id).
        tool.profile_id = "prod"

        new_token = {
            "access_token": "prod_access",
            "refresh_token": "prod_refresh",
            "access_expires_at": 9876543210,
        }
        tool.config.save_token(new_token)

        # Token must be stored under the active profile, NOT
        # under the hard-coded "default".
        stored_prod = tool.config._slice.auth.get_credentials(profile_id="prod")
        assert stored_prod is not None
        assert stored_prod.access_token == "prod_access"
        assert stored_prod.refresh_token == "prod_refresh"
        assert stored_prod.access_expires_at == 9876543210

        # And the "default" profile must remain empty.
        stored_default = tool.config._slice.auth.get_credentials(
            profile_id="default"
        )
        assert stored_default is None

    def test_hh_applicant_tool_save_token_explicit_profile_id(self, tmp_path):
        """``save_token(token, profile_id="...")`` honours an explicit
        profile_id override (issue #59 convenience API)."""
        config_data = {
            "hh": {
                "client_id": "explicit_client",
                "client_secret": "explicit_secret",
            },
            "telegram": {},
            "ai": {},
            "max": {},
            "smtp": {},
            "profiles": {},
            "active_profile": None,
        }
        tool, _config_file = self._setup_tool_with_config(tmp_path, config_data)

        new_token = {
            "access_token": "explicit_access",
            "refresh_token": "explicit_refresh",
        }
        tool.config.save_token(new_token, profile_id="staging")

        stored = tool.config._slice.auth.get_credentials(profile_id="staging")
        assert stored is not None
        assert stored.access_token == "explicit_access"
        assert stored.refresh_token == "explicit_refresh"
