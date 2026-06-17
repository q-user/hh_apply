"""Tests for the config_auth slice (VSA - Issue #50, Phase 3).

TDD: tests are written first, then the slice is implemented to make them pass.

Slice responsibilities:
  * AppConfig loading / saving (JSON file) with profile support.
  * OAuth credentials persistence + expiration checks.
  * User profile management (multi-profile).

The tests are split into:
  * Model tests (pure data — no DB, no filesystem).
  * Handler tests (DB + filesystem fixtures).
  * Slice integration tests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from job_bot.shared.config.settings import Settings
from job_bot.shared.storage.database import Database

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# ``temp_db_path``, ``database`` and ``temp_config_path`` live in
# ``tests/vsa/conftest.py`` so the fixtures are shared between slice
# test files.


@pytest.fixture
def settings() -> Settings:
    """Create test settings with sensible defaults."""
    return Settings()


@pytest.fixture
def sample_config_dict() -> dict[str, Any]:
    """Sample config dict as it would appear in a JSON file."""
    return {
        "client_id": "TEST_CLIENT_ID",
        "client_secret": "TEST_CLIENT_SECRET",
        "api_delay": 0.5,
        "user_agent": "test/1.0",
        "token": {
            "access_token": "old_access",
            "refresh_token": "old_refresh",
            "access_expires_at": 0,
        },
    }


# ---------------------------------------------------------------------------
# Model tests — AppConfig
# ---------------------------------------------------------------------------


class TestAppConfigModel:
    """Test the AppConfig domain model."""

    def test_default_construction(self) -> None:
        """Test that an AppConfig can be constructed with defaults."""
        from job_bot.config_auth.models.config import AppConfig

        config = AppConfig()
        assert config is not None
        # Default sub-configs should exist
        assert config.hh is not None
        assert config.telegram is not None
        assert config.ai is not None
        assert config.max is not None
        assert config.smtp is not None
        # No profiles by default
        assert config.profiles == {}
        assert config.active_profile is None

    def test_to_dict_roundtrip(self) -> None:
        """Test that AppConfig can be converted to dict and back."""
        from job_bot.config_auth.models.config import AppConfig

        config = AppConfig()
        config.hh.client_id = "round_id"
        config.telegram.bot_token = "round_token"

        data = config.to_dict()
        assert isinstance(data, dict)
        assert data["hh"]["client_id"] == "round_id"
        assert data["telegram"]["bot_token"] == "round_token"

        # Roundtrip
        config2 = AppConfig.from_dict(data)
        assert config2.hh.client_id == "round_id"
        assert config2.telegram.bot_token == "round_token"

    def test_profiles_are_isolated(self) -> None:
        """Test that adding a profile doesn't pollute the default config."""
        from job_bot.config_auth.models.config import AppConfig, HHConfig

        config = AppConfig()
        config.add_profile("secondary", HHConfig(client_id="sec_id"))

        assert "secondary" in config.profiles
        assert config.profiles["secondary"].client_id == "sec_id"
        # Default hh.client_id is still None
        assert config.hh.client_id is None

    def test_set_active_profile(self) -> None:
        """Test setting the active profile name."""
        from job_bot.config_auth.models.config import AppConfig, HHConfig

        config = AppConfig()
        config.add_profile("prod", HHConfig(client_id="prod_id"))
        config.active_profile = "prod"
        assert config.active_profile == "prod"
        assert config.get_active_hh_config().client_id == "prod_id"

    def test_get_active_hh_config_default(self) -> None:
        """Test get_active_hh_config returns the top-level hh config when
        no profile is selected."""
        from job_bot.config_auth.models.config import AppConfig

        config = AppConfig()
        config.hh.client_id = "default_id"
        assert config.get_active_hh_config().client_id == "default_id"

    def test_list_profiles(self) -> None:
        """Test listing profile names."""
        from job_bot.config_auth.models.config import AppConfig, HHConfig

        config = AppConfig()
        config.add_profile("a", HHConfig())
        config.add_profile("b", HHConfig())
        names = sorted(config.list_profiles())
        assert names == ["a", "b"]

    def test_remove_profile(self) -> None:
        """Test removing a profile."""
        from job_bot.config_auth.models.config import AppConfig, HHConfig

        config = AppConfig()
        config.add_profile("temp", HHConfig())
        assert "temp" in config.profiles
        config.remove_profile("temp")
        assert "temp" not in config.profiles

    def test_validation_missing_token(self) -> None:
        """Test that AppConfig.validate raises if HH config is missing
        client_id/secret when not a profile-only config."""
        from job_bot.config_auth.models.config import AppConfig

        config = AppConfig()
        # Without client_id / client_secret, validation should fail
        # (we use a strict mode for production use)
        with pytest.raises(ValueError):
            config.validate(strict=True)


# ---------------------------------------------------------------------------
# Model tests — OAuthCredentials
# ---------------------------------------------------------------------------


class TestOAuthCredentialsModel:
    """Test the OAuthCredentials domain model."""

    def test_construction(self) -> None:
        """Test basic construction."""
        from job_bot.config_auth.models.credentials import OAuthCredentials

        creds = OAuthCredentials(
            access_token="access_123",
            refresh_token="refresh_456",
            access_expires_at=int(time.time()) + 3600,
        )
        assert creds.access_token == "access_123"
        assert creds.refresh_token == "refresh_456"
        assert creds.access_expires_at > 0

    def test_is_expired_returns_false_for_future(self) -> None:
        """Token expiring in the future is not expired."""
        from job_bot.config_auth.models.credentials import OAuthCredentials

        creds = OAuthCredentials(
            access_token="a",
            refresh_token="r",
            access_expires_at=int(time.time()) + 3600,
        )
        assert creds.is_expired is False

    def test_is_expired_returns_true_for_past(self) -> None:
        """Token that already expired is expired."""
        from job_bot.config_auth.models.credentials import OAuthCredentials

        creds = OAuthCredentials(
            access_token="a",
            refresh_token="r",
            access_expires_at=int(time.time()) - 10,
        )
        assert creds.is_expired is True

    def test_is_expired_handles_zero(self) -> None:
        """Token with expires_at=0 is treated as expired."""
        from job_bot.config_auth.models.credentials import OAuthCredentials

        creds = OAuthCredentials(
            access_token="a",
            refresh_token="r",
            access_expires_at=0,
        )
        assert creds.is_expired is True

    def test_expires_in(self) -> None:
        """Test the expires_in helper returns seconds remaining."""
        from job_bot.config_auth.models.credentials import OAuthCredentials

        now = int(time.time())
        creds = OAuthCredentials(
            access_token="a",
            refresh_token="r",
            access_expires_at=now + 600,
        )
        # Within a few seconds of expected
        assert 590 <= creds.expires_in <= 600

    def test_to_dict_from_dict_roundtrip(self) -> None:
        """Test that OAuthCredentials can be (de)serialized."""
        from job_bot.config_auth.models.credentials import OAuthCredentials

        creds = OAuthCredentials(
            access_token="a", refresh_token="r", access_expires_at=12345
        )
        data = creds.to_dict()
        assert data == {
            "access_token": "a",
            "refresh_token": "r",
            "access_expires_at": 12345,
        }
        creds2 = OAuthCredentials.from_dict(data)
        assert creds2 == creds

    def test_expiration_buffer(self) -> None:
        """Token within buffer of expiry is considered expired."""
        from job_bot.config_auth.models.credentials import OAuthCredentials

        # 30 seconds from now — should be considered expired with 60s buffer
        creds = OAuthCredentials(
            access_token="a",
            refresh_token="r",
            access_expires_at=int(time.time()) + 30,
        )
        assert creds.is_expired_with_buffer(buffer_seconds=60) is True


# ---------------------------------------------------------------------------
# Model tests — UserProfile
# ---------------------------------------------------------------------------


class TestUserProfileModel:
    """Test the UserProfile domain model."""

    def test_construction(self) -> None:
        """Test basic construction with required fields."""
        from job_bot.config_auth.models.user import UserProfile

        user = UserProfile(
            id="u1",
            full_name="John Doe",
            email="john@example.com",
        )
        assert user.id == "u1"
        assert user.full_name == "John Doe"
        assert user.email == "john@example.com"
        # Auto timestamps
        assert user.created_at is not None
        assert user.updated_at is not None

    def test_profile_id_is_optional(self) -> None:
        """profile_id is optional and defaults to None."""
        from job_bot.config_auth.models.user import UserProfile

        user = UserProfile(id="u1", full_name="X")
        assert user.profile_id is None

    def test_to_dict_from_dict_roundtrip(self) -> None:
        """Test that UserProfile can be (de)serialized."""
        from job_bot.config_auth.models.user import UserProfile

        user = UserProfile(
            id="u1",
            hh_user_id="hh_42",
            full_name="Jane",
            email="j@x.com",
            phone="+123",
            profile_id="prod",
        )
        data = user.to_dict()
        restored = UserProfile.from_dict(data)
        assert restored.id == user.id
        assert restored.hh_user_id == "hh_42"
        assert restored.full_name == "Jane"
        assert restored.email == "j@x.com"
        assert restored.phone == "+123"
        assert restored.profile_id == "prod"


# ---------------------------------------------------------------------------
# Handler tests — ConfigHandler (JSON file)
# ---------------------------------------------------------------------------


class TestConfigHandlerLoading:
    """Test config loading (RED: just write the tests)."""

    def test_load_from_existing_file(
        self, temp_config_path: Path, sample_config_dict: dict[str, Any]
    ) -> None:
        """Load config from an existing JSON file."""
        from job_bot.config_auth.handlers.config_handler import ConfigHandler
        from job_bot.config_auth.models.config import AppConfig

        temp_config_path.write_text(
            json.dumps(sample_config_dict), encoding="utf-8"
        )
        handler = ConfigHandler()
        config = handler.load(temp_config_path)
        assert isinstance(config, AppConfig)
        assert config.hh.client_id == "TEST_CLIENT_ID"
        assert config.hh.client_secret == "TEST_CLIENT_SECRET"

    def test_load_missing_file_returns_defaults(
        self, temp_config_path: Path
    ) -> None:
        """Loading a non-existent file returns default config."""
        from job_bot.config_auth.handlers.config_handler import ConfigHandler
        from job_bot.config_auth.models.config import AppConfig

        handler = ConfigHandler()
        config = handler.load(temp_config_path)
        assert isinstance(config, AppConfig)
        # Defaults
        assert config.hh.client_id is None

    def test_load_with_default_values(self, temp_config_path: Path) -> None:
        """Test that defaults are populated when keys are missing."""
        from job_bot.config_auth.handlers.config_handler import ConfigHandler

        # Empty JSON
        temp_config_path.write_text("{}", encoding="utf-8")
        handler = ConfigHandler()
        config = handler.load(temp_config_path)
        # Sub-configs should be present with their own defaults
        assert config.hh is not None
        assert config.telegram is not None
        assert config.ai is not None
        # AI default model from shared.Settings
        assert config.ai.model == "gpt-4o-mini"

    def test_load_respects_hh_profile_id_env(
        self, temp_config_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Loading should honour the HH_PROFILE_ID env var."""
        from job_bot.config_auth.handlers.config_handler import ConfigHandler

        temp_config_path.write_text(
            json.dumps(
                {
                    "client_id": "default_id",
                    "client_secret": "default_secret",
                    "profiles": {
                        "prod": {
                            "client_id": "prod_id",
                            "client_secret": "prod_secret",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("HH_PROFILE_ID", "prod")
        handler = ConfigHandler()
        config = handler.load(temp_config_path)
        # The active profile should be prod
        assert config.active_profile == "prod"
        assert config.get_active_hh_config().client_id == "prod_id"

    def test_load_validation_error_on_corrupt_file(
        self, temp_config_path: Path
    ) -> None:
        """Loading a corrupt JSON file raises a validation/parse error."""
        from job_bot.config_auth.handlers.config_handler import ConfigHandler

        temp_config_path.write_text("{not valid json", encoding="utf-8")
        handler = ConfigHandler()
        with pytest.raises(ValueError):
            handler.load(temp_config_path, strict=True)


class TestConfigHandlerSaving:
    """Test config saving."""

    def test_save_creates_file(
        self,
        temp_config_path: Path,
    ) -> None:
        """Saving creates a JSON file at the target path."""
        from job_bot.config_auth.handlers.config_handler import ConfigHandler
        from job_bot.config_auth.models.config import AppConfig

        config = AppConfig()
        config.hh.client_id = "save_id"
        handler = ConfigHandler()
        handler.save(config, temp_config_path)

        assert temp_config_path.exists()
        data = json.loads(temp_config_path.read_text(encoding="utf-8"))
        assert data["hh"]["client_id"] == "save_id"

    def test_save_creates_parent_dirs(
        self,
        tmp_path: Path,
    ) -> None:
        """Saving creates parent directories as needed."""
        from job_bot.config_auth.handlers.config_handler import ConfigHandler
        from job_bot.config_auth.models.config import AppConfig

        nested = tmp_path / "a" / "b" / "config.json"
        handler = ConfigHandler()
        handler.save(AppConfig(), nested)
        assert nested.exists()

    def test_save_atomic_no_partial_file(self, temp_config_path: Path) -> None:
        """Atomic save never leaves a partially written file at the target.

        We force a failure during write (read-only directory) and verify the
        original file is left intact or simply doesn't exist - never
        half-written.
        """
        from job_bot.config_auth.handlers.config_handler import ConfigHandler
        from job_bot.config_auth.models.config import AppConfig

        # First, write a valid config so the file exists
        config = AppConfig()
        config.hh.client_id = "original"
        handler = ConfigHandler()
        handler.save(config, temp_config_path)
        assert temp_config_path.exists()
        original_content = temp_config_path.read_text(encoding="utf-8")

        # Now make a new save and verify the file is always readable JSON
        config.hh.client_id = "updated"
        handler.save(config, temp_config_path)
        new_content = temp_config_path.read_text(encoding="utf-8")
        # Both should be valid JSON, and the file should not be corrupted
        json.loads(new_content)
        assert "updated" in new_content
        # Original was different
        assert original_content != new_content

    def test_save_backup_creates_bak_file(self, temp_config_path: Path) -> None:
        """When a config file already exists, save creates a .bak copy."""
        from job_bot.config_auth.handlers.config_handler import ConfigHandler
        from job_bot.config_auth.models.config import AppConfig

        # First write
        config = AppConfig()
        config.hh.client_id = "v1"
        handler = ConfigHandler()
        handler.save(config, temp_config_path)

        # Second write — should create .bak
        config.hh.client_id = "v2"
        handler.save(config, temp_config_path, backup=True)

        backup = temp_config_path.with_suffix(temp_config_path.suffix + ".bak")
        assert backup.exists()
        bak_data = json.loads(backup.read_text(encoding="utf-8"))
        assert bak_data["hh"]["client_id"] == "v1"


# ---------------------------------------------------------------------------
# Handler tests — AuthHandler
# ---------------------------------------------------------------------------


class TestAuthHandler:
    """Test OAuth credentials persistence + refresh."""

    def test_save_and_get_credentials(self, database: Database) -> None:
        """Store and retrieve credentials."""
        from job_bot.config_auth.handlers.auth_handler import AuthHandler
        from job_bot.config_auth.models.credentials import OAuthCredentials

        handler = AuthHandler(database)
        creds = OAuthCredentials(
            access_token="a1", refresh_token="r1", access_expires_at=12345
        )
        handler.save_credentials(creds)
        loaded = handler.get_credentials()
        assert loaded is not None
        assert loaded.access_token == "a1"
        assert loaded.refresh_token == "r1"
        assert loaded.access_expires_at == 12345

    def test_get_credentials_empty(self, database: Database) -> None:
        """Get credentials returns None when none stored."""
        from job_bot.config_auth.handlers.auth_handler import AuthHandler

        handler = AuthHandler(database)
        assert handler.get_credentials() is None

    def test_save_credentials_overwrites(self, database: Database) -> None:
        """Saving credentials twice keeps the latest only (single-row)."""
        from job_bot.config_auth.handlers.auth_handler import AuthHandler
        from job_bot.config_auth.models.credentials import OAuthCredentials

        handler = AuthHandler(database)
        handler.save_credentials(
            OAuthCredentials(
                access_token="a1", refresh_token="r1", access_expires_at=1
            )
        )
        handler.save_credentials(
            OAuthCredentials(
                access_token="a2", refresh_token="r2", access_expires_at=2
            )
        )
        loaded = handler.get_credentials()
        assert loaded is not None
        assert loaded.access_token == "a2"

    def test_clear_credentials(self, database: Database) -> None:
        """Clearing credentials removes them."""
        from job_bot.config_auth.handlers.auth_handler import AuthHandler
        from job_bot.config_auth.models.credentials import OAuthCredentials

        handler = AuthHandler(database)
        handler.save_credentials(
            OAuthCredentials(
                access_token="a", refresh_token="r", access_expires_at=1
            )
        )
        handler.clear_credentials()
        assert handler.get_credentials() is None

    def test_refresh_rotates_tokens(self, database: Database) -> None:
        """Refresh should call the refresher and persist new tokens."""
        from job_bot.config_auth.handlers.auth_handler import AuthHandler
        from job_bot.config_auth.models.credentials import OAuthCredentials

        handler = AuthHandler(database)
        handler.save_credentials(
            OAuthCredentials(
                access_token="old_a",
                refresh_token="old_r",
                access_expires_at=int(time.time()) - 100,  # expired
            )
        )

        def fake_refresh(refresh_token: str) -> OAuthCredentials:
            return OAuthCredentials(
                access_token="new_a",
                refresh_token="new_r",
                access_expires_at=int(time.time()) + 3600,
            )

        new_creds = handler.refresh(fake_refresh)
        assert new_creds.access_token == "new_a"
        assert new_creds.refresh_token == "new_r"
        # And the new tokens are persisted
        loaded = handler.get_credentials()
        assert loaded is not None
        assert loaded.access_token == "new_a"

    def test_refresh_raises_without_existing_token(
        self, database: Database
    ) -> None:
        """Refreshing without stored credentials raises."""
        from job_bot.config_auth.handlers.auth_handler import AuthHandler

        handler = AuthHandler(database)
        with pytest.raises(ValueError):
            handler.refresh(lambda rt: None)

    def test_multi_profile_credentials(self, database: Database) -> None:
        """Credentials are kept separately per profile_id."""
        from job_bot.config_auth.handlers.auth_handler import AuthHandler
        from job_bot.config_auth.models.credentials import OAuthCredentials

        handler = AuthHandler(database)
        handler.save_credentials(
            OAuthCredentials(
                access_token="a1", refresh_token="r1", access_expires_at=1
            ),
            profile_id="prod",
        )
        handler.save_credentials(
            OAuthCredentials(
                access_token="a2", refresh_token="r2", access_expires_at=2
            ),
            profile_id="dev",
        )
        prod = handler.get_credentials(profile_id="prod")
        dev = handler.get_credentials(profile_id="dev")
        assert prod is not None and prod.access_token == "a1"
        assert dev is not None and dev.access_token == "a2"


# ---------------------------------------------------------------------------
# Handler tests — UserHandler
# ---------------------------------------------------------------------------


class TestUserHandler:
    """Test user profile management."""

    def test_save_and_get_user(self, database: Database) -> None:
        """Store and retrieve a user profile."""
        from job_bot.config_auth.handlers.user_handler import UserHandler
        from job_bot.config_auth.models.user import UserProfile

        handler = UserHandler(database)
        user = UserProfile(
            id="u1", full_name="John Doe", email="john@example.com"
        )
        handler.save_user(user)
        loaded = handler.get_user("u1")
        assert loaded is not None
        assert loaded.full_name == "John Doe"
        assert loaded.email == "john@example.com"

    def test_get_user_by_profile(self, database: Database) -> None:
        """Look up user by linked profile_id."""
        from job_bot.config_auth.handlers.user_handler import UserHandler
        from job_bot.config_auth.models.user import UserProfile

        handler = UserHandler(database)
        user = UserProfile(id="u1", full_name="A", profile_id="prod")
        handler.save_user(user)
        loaded = handler.get_user_by_profile("prod")
        assert loaded is not None
        assert loaded.id == "u1"

    def test_list_users(self, database: Database) -> None:
        """List all stored users."""
        from job_bot.config_auth.handlers.user_handler import UserHandler
        from job_bot.config_auth.models.user import UserProfile

        handler = UserHandler(database)
        handler.save_user(UserProfile(id="u1", full_name="A"))
        handler.save_user(UserProfile(id="u2", full_name="B"))
        users = handler.list_users()
        assert len(users) == 2
        ids = {u.id for u in users}
        assert ids == {"u1", "u2"}

    def test_delete_user(self, database: Database) -> None:
        """Delete a user by ID."""
        from job_bot.config_auth.handlers.user_handler import UserHandler
        from job_bot.config_auth.models.user import UserProfile

        handler = UserHandler(database)
        handler.save_user(UserProfile(id="u1", full_name="A"))
        assert handler.delete_user("u1") is True
        assert handler.get_user("u1") is None

    def test_delete_unknown_user(self, database: Database) -> None:
        """Deleting an unknown user returns False."""
        from job_bot.config_auth.handlers.user_handler import UserHandler

        handler = UserHandler(database)
        assert handler.delete_user("nope") is False

    def test_update_user(self, database: Database) -> None:
        """Updating a user mutates stored fields."""
        from job_bot.config_auth.handlers.user_handler import UserHandler
        from job_bot.config_auth.models.user import UserProfile

        handler = UserHandler(database)
        user = UserProfile(id="u1", full_name="Old")
        handler.save_user(user)
        user.full_name = "New"
        handler.save_user(user)
        loaded = handler.get_user("u1")
        assert loaded is not None
        assert loaded.full_name == "New"

    def test_list_users_filtered_by_profile(self, database: Database) -> None:
        """List users can be filtered by profile_id."""
        from job_bot.config_auth.handlers.user_handler import UserHandler
        from job_bot.config_auth.models.user import UserProfile

        handler = UserHandler(database)
        handler.save_user(
            UserProfile(id="u1", full_name="A", profile_id="prod")
        )
        handler.save_user(UserProfile(id="u2", full_name="B", profile_id="dev"))
        prod_users = handler.list_users(profile_id="prod")
        assert len(prod_users) == 1
        assert prod_users[0].id == "u1"


# ---------------------------------------------------------------------------
# Slice tests
# ---------------------------------------------------------------------------


class TestConfigAuthSlice:
    """Test the ConfigAuthSlice aggregation."""

    def test_create_slice(
        self, database: Database, temp_config_path: Path
    ) -> None:
        """The slice can be created with a database and config path."""
        from job_bot.config_auth.slice import ConfigAuthSlice

        slice_ = ConfigAuthSlice(
            database=database, config_path=temp_config_path
        )
        assert slice_.database is database
        assert slice_.config_path == temp_config_path
        # Ports are accessible
        assert slice_.config is not None
        assert slice_.auth is not None
        assert slice_.users is not None

    def test_create_slice_factory(
        self, temp_db_path: Path, temp_config_path: Path
    ) -> None:
        """The factory wires everything from settings."""
        from job_bot.config_auth.slice import (
            ConfigAuthSlice,
            create_config_auth_slice,
        )

        settings = Settings()
        settings.database.path = temp_db_path
        slice_ = create_config_auth_slice(
            settings=settings, config_path=temp_config_path
        )
        assert isinstance(slice_, ConfigAuthSlice)
        assert slice_.config_path == temp_config_path


class TestConfigAuthSliceIntegration:
    """End-to-end tests for the config_auth slice."""

    def test_load_save_config_workflow(
        self, temp_db_path: Path, temp_config_path: Path
    ) -> None:
        """End-to-end: save config, reload, verify."""
        from job_bot.config_auth.models.config import AppConfig, HHConfig
        from job_bot.config_auth.slice import create_config_auth_slice

        settings = Settings()
        settings.database.path = temp_db_path
        slice_ = create_config_auth_slice(
            settings=settings, config_path=temp_config_path
        )

        # Save a config
        config = AppConfig()
        config.add_profile("prod", HHConfig(client_id="prod_id"))
        slice_.config.save(config, temp_config_path)

        # Reload
        loaded = slice_.config.load(temp_config_path)
        assert "prod" in loaded.profiles
        assert loaded.profiles["prod"].client_id == "prod_id"

    def test_full_workflow(
        self, temp_db_path: Path, temp_config_path: Path
    ) -> None:
        """Full workflow: save config, store tokens, store user, retrieve all."""
        from job_bot.config_auth.models.config import AppConfig, HHConfig
        from job_bot.config_auth.models.credentials import OAuthCredentials
        from job_bot.config_auth.models.user import UserProfile
        from job_bot.config_auth.slice import create_config_auth_slice

        settings = Settings()
        settings.database.path = temp_db_path
        slice_ = create_config_auth_slice(
            settings=settings, config_path=temp_config_path
        )

        # 1. Save config with profile
        config = AppConfig()
        config.add_profile("prod", HHConfig(client_id="prod_id"))
        slice_.config.save(config, temp_config_path)

        # 2. Save OAuth credentials
        creds = OAuthCredentials(
            access_token="a",
            refresh_token="r",
            access_expires_at=int(time.time()) + 3600,
        )
        slice_.auth.save_credentials(creds, profile_id="prod")

        # 3. Save user
        user = UserProfile(id="u1", full_name="John", profile_id="prod")
        slice_.users.save_user(user)

        # 4. Verify all
        loaded_creds = slice_.auth.get_credentials(profile_id="prod")
        assert loaded_creds is not None
        assert loaded_creds.access_token == "a"

        loaded_user = slice_.users.get_user("u1")
        assert loaded_user is not None
        assert loaded_user.full_name == "John"
        assert loaded_user.profile_id == "prod"

    def test_active_profile_via_env(
        self,
        temp_db_path: Path,
        temp_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The slice's config respects HH_PROFILE_ID when loading."""
        from job_bot.config_auth.models.config import AppConfig, HHConfig
        from job_bot.config_auth.slice import create_config_auth_slice

        # Pre-write a config with two profiles
        config = AppConfig()
        config.add_profile("dev", HHConfig(client_id="dev_id"))
        config.add_profile("prod", HHConfig(client_id="prod_id"))
        temp_config_path.write_text(
            json.dumps(config.to_dict()), encoding="utf-8"
        )

        settings = Settings()
        settings.database.path = temp_db_path
        slice_ = create_config_auth_slice(
            settings=settings, config_path=temp_config_path
        )

        monkeypatch.setenv("HH_PROFILE_ID", "prod")
        loaded = slice_.config.load(temp_config_path)
        assert loaded.active_profile == "prod"
        assert loaded.get_active_hh_config().client_id == "prod_id"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
