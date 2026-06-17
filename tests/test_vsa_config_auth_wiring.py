"""Tests for ConfigAuthSlice wiring through the slim AppContainer (issue #155).

The new :class:`job_bot.container.AppContainer` is a pure-VSA composition
root. It exposes a ``config_auth`` :func:`@cached_property` slice accessor
and the 4 legacy ``_Adapter`` shim classes (``_ConfigAdapter`` and
friends) are deleted.

The ``HHApplicantTool.config`` integration tests previously lived here as
:class:`TestHHApplicantToolConfigSwitchover` — those exercised the
``_ConfigAdapter`` dict-like surface. Since ``_ConfigAdapter`` is gone,
those tests are removed in issue #155. The new ``ConfigAuthSlice`` is
exercised by ``tests/vsa/test_job_bot_container.py::TestSliceAccessors``,
and the dict-like config flows are covered by the slice-level tests in
``tests/test_vsa_config_auth_slice_*.py``.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestConfigAuthSliceWiring:
    """Tests that the slim :class:`AppContainer` wires the
    :class:`ConfigAuthSlice` via the ``config_auth`` cached_property
    (issue #155)."""

    def _make_mock_tool(self, config_dir: Path | None = None):
        """Create a mock ``HHApplicantTool`` with all required attributes.

        The slim container resolves the VSA slice's config path via
        ``tool.config_path / CONFIG_FILENAME`` (where ``tool.config_path``
        is built from ``tool.config_dir / tool.profile_id``). Tests that
        exercise the slice end-to-end must therefore set ``config_dir``
        and ``profile_id`` on the tool.

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
        """Create a per-profile config layout ``<tmp>/default/config.json``."""
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
        """``AppContainer.config_auth`` returns a :class:`ConfigAuthSlice`."""
        from job_bot.config_auth.slice import ConfigAuthSlice
        from job_bot.container import AppContainer

        tool = self._make_mock_tool()

        container = AppContainer(tool)
        slice_ = container.config_auth

        assert isinstance(slice_, ConfigAuthSlice)
        assert hasattr(slice_, "config")
        assert hasattr(slice_, "auth")
        assert hasattr(slice_, "users")

    def test_config_auth_slice_round_trip(self):
        """``config_auth`` slice can load + save the on-disk config.

        Issue #155 deletes the legacy ``_ConfigAdapter`` (the dict-like
        shim). The slice is the single source of truth for config
        round-trips; legacy callers must migrate to the slice's
        :class:`ConfigHandler` / :class:`AuthHandler` ports directly
        (this is the contract used by ``hh_applicant_tool.main``'s
        VSA-native ``__main__`` path).
        """
        from job_bot.container import AppContainer

        config_dir = self._create_test_config_dir()
        try:
            tool = self._make_mock_tool(config_dir=config_dir)

            container = AppContainer(tool)
            slice_ = container.config_auth

            # Load and verify a sub-config round-trip.
            config = slice_.config.load(slice_.config_path)
            assert config.hh.client_id == "test_client"
            assert config.telegram.bot_token == "test_bot_token"

            # Save a new value and re-read.
            config.telegram.bot_token = "updated_token"
            slice_.config.save(config, slice_.config_path, backup=True)

            on_disk = json.loads(
                (config_dir / "default" / "config.json").read_text()
            )
            assert on_disk["telegram"]["bot_token"] == "updated_token"
        finally:
            shutil.rmtree(config_dir, ignore_errors=True)
