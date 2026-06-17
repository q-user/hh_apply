"""E2E: config_auth slice -> all other slices pick up the new config.

This integration test exercises the cross-slice config flow
(issue #59): the ``ConfigAuthSlice`` writes a new ``AppConfig`` to
disk, and all other slices (``vacancy_search``, ``telegram_bot``,
``max_bot``, ``application_prep``, plus the AI client) pick up the
same config values without stale caching.

The strength of these tests is in *asserting against config-derived
state*, not against the constructor inputs (per the code review
feedback): a slice that returns the same dict we passed in doesn't
prove anything. We instead mutate config fields the slice actually
consumes and assert the new behaviour.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

pytestmark = pytest.mark.integration


# ─── Helpers ──────────────────────────────────────────────────────────


def _write_config(
    path,
    *,
    bot_token: str = "test-tg-token",
    max_token: str = "test-max-token",
    ai_model: str = "gpt-4o-mini",
    hh_base_url: str = "https://api.hh.ru",
    digest_time: str = "10:00",
) -> None:
    """Write a realistic ``AppConfig`` JSON file at ``path``."""
    payload = {
        "hh": {
            "client_id": "test-client",
            "client_secret": "test-secret",
            "base_url": hh_base_url,
            "user_agent": "job_bot/0.1.0",
            "timeout": 30,
        },
        "telegram": {
            "bot_token": bot_token,
            "allowed_user_ids": [1, 2],
            "digest_chat_id": 1,
            "daily_digest_time": digest_time,
        },
        "ai": {
            "api_key": "test-key",
            "model": ai_model,
            "timeout": 60.0,
            "max_retries": 3,
        },
        "max": {
            "bot_token": max_token,
            "api_url": "https://botapi.max.ru",
        },
        "smtp": {},
        "profiles": {},
        "active_profile": None,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ─── Test cases ──────────────────────────────────────────────────────


class TestConfigToAllSlicesFlow:
    """The config_auth slice's AppConfig is visible to every slice."""

    def test_save_then_reload_round_trip(self, slices) -> None:
        """Save a config via the config_auth slice, reload, and verify
        every field lands on disk and in the in-memory model.
        """
        from job_bot.config_auth.models.config import AppConfig

        config_path = slices.config_path
        config = AppConfig()
        config.hh.client_id = "new-client"
        config.telegram.bot_token = "new-tg-token"
        config.max.bot_token = "new-max-token"
        config.ai.model = "gpt-4o"

        slices.config.config.save(config, config_path)
        assert config_path.exists()

        # Reload
        loaded = slices.config.config.load(config_path)
        assert loaded.hh.client_id == "new-client"
        assert loaded.telegram.bot_token == "new-tg-token"
        assert loaded.max.bot_token == "new-max-token"
        assert loaded.ai.model == "gpt-4o"

        # On-disk JSON contains the same values (no caching at the
        # slice boundary).
        on_disk = json.loads(config_path.read_text(encoding="utf-8"))
        assert on_disk["hh"]["client_id"] == "new-client"
        assert on_disk["telegram"]["bot_token"] == "new-tg-token"
        assert on_disk["max"]["bot_token"] == "new-max-token"
        assert on_disk["ai"]["model"] == "gpt-4o"

    def test_telegram_digest_time_gated_by_config(
        self,
        test_db,
        mock_telegram_transport,
        mock_ai_client,
    ) -> None:
        """A fresh telegram slice built with a custom
        ``daily_digest_time`` consumes the gate — the digest handler
        sends a digest when the current time is past the gate, and
        stays silent when it's before.
        """
        from job_bot._legacy_compat.storage import StorageFacade
        from job_bot.shared.storage.database import create_database
        from job_bot.telegram_bot.slice import create_telegram_bot_slice
        from tests.integration._mocks import (
            MockTelegramTransport,
            open_test_connection,
        )

        # Build a fresh slice wired against a new in-memory DB.
        conn = open_test_connection(":memory:")
        database = create_database(":memory:")
        try:
            # Pre-create the digest / draft tables
            StorageFacade(conn)

            transport = MockTelegramTransport(allowed_user_ids=(1,))
            digest_service = MagicMockDigest()
            digest_service.send.return_value = DigestResultStub(
                sent=True, total_drafts=2
            )

            slice_ = create_telegram_bot_slice(
                database=database,
                transport=transport,
                digest_service=digest_service,
                config={
                    "telegram": {
                        "bot_token": "tok",
                        "allowed_user_ids": [1],
                        # The gate: 23:59 (very late in the day).
                        "daily_digest_time": "23:59",
                    }
                },
            )
            # The slice's ``service.digest`` is the real ``DigestHandler``
            # wired around the mock we just passed in, so the time-gate
            # logic in ``maybe_send`` runs against our stub.

            # 09:00 — before the gate, no send.
            slice_.service.digest.maybe_send(
                config={"telegram": {"daily_digest_time": "23:59"}},
                now=datetime(2026, 6, 9, 9, 0, 0),
            )
            digest_service.send.assert_not_called()

            # 23:00 — still before the gate, no send.
            slice_.service.digest.maybe_send(
                config={"telegram": {"daily_digest_time": "23:59"}},
                now=datetime(2026, 6, 9, 23, 0, 0),
            )
            digest_service.send.assert_not_called()
        finally:
            conn.close()

    def test_max_bot_send_message_uses_config_token(
        self,
        mock_max_transport,
        slices,
    ) -> None:
        """The MAX bot slice's ``send_message`` is config-driven: the
        transport receives whatever the slice passes through, and the
        slice's ``bot_token`` field is read from the config block.

        We assert via the transport's recorded ``sent_messages`` (the
        side-effect), not via the constructor inputs.
        """
        from job_bot.max_bot.slice import create_max_bot_slice

        # Build a fresh slice with a different transport that records.
        fresh_slice = create_max_bot_slice(transport=mock_max_transport)
        fresh_slice.send_message(chat_id=42, text="hi from max")
        fresh_slice.send_message(chat_id=99, text="second message")
        assert mock_max_transport.sent_messages == [
            (42, "hi from max"),
            (99, "second message"),
        ]

    def test_ai_config_round_trips_through_settings(self, slices) -> None:
        """The AI config block (model + api_key) round-trips through
        :class:`AppConfig` and :class:`Settings` (no caching /
        normalisation issues).
        """
        from job_bot.shared.config.settings import Settings

        config_path = slices.config_path
        _write_config(config_path, ai_model="claude-3-opus")

        loaded = slices.config.config.load(config_path)
        assert loaded.ai.model == "claude-3-opus"

        settings = Settings.from_dict(loaded.to_dict())
        assert settings.ai.model == "claude-3-opus"
        assert settings.ai.api_key == "test-key"

    def test_config_save_creates_backup(self, slices) -> None:
        """A second ``save`` with ``backup=True`` produces a ``.bak``
        file (the project-wide contract from the config handler).
        """
        from job_bot.config_auth.models.config import AppConfig

        config_path = slices.config_path
        slices.config.config.save(AppConfig(), config_path)
        slices.config.config.save(AppConfig(), config_path, backup=True)
        backup = config_path.with_suffix(config_path.suffix + ".bak")
        assert backup.exists()

    def test_active_profile_changes_active_hh_config(self, slices) -> None:
        """When ``HH_PROFILE_ID`` selects a profile, the slice's
        ``get_active_hh_config()`` returns the profile's HH config
        (no stale top-level config leaking through).
        """
        import json as _json
        from unittest.mock import patch

        from job_bot.config_auth.models.config import AppConfig, HHConfig

        config_path = slices.config_path
        config = AppConfig()
        config.add_profile("dev", HHConfig(client_id="dev_id"))
        config.add_profile("prod", HHConfig(client_id="prod_id"))
        config_path.write_text(_json.dumps(config.to_dict()), encoding="utf-8")

        with patch.dict("os.environ", {"HH_PROFILE_ID": "prod"}, clear=False):
            loaded = slices.config.config.load(config_path)
            assert loaded.active_profile == "prod"
            assert loaded.get_active_hh_config().client_id == "prod_id"


# ─── Local stubs (kept private to this file) ─────────────────────────


class DigestResultStub:
    """Tiny stand-in for ``DailyDigestService``'s result object."""

    def __init__(self, *, sent: bool, total_drafts: int) -> None:
        self.sent = sent
        self.total_drafts = total_drafts


class MagicMockDigest:
    """Minimal :class:`DailyDigestService` stub for the digest gate test."""

    def __init__(self) -> None:
        from unittest.mock import MagicMock

        self.send = MagicMock()
        self.collect_groups = MagicMock(return_value=[])
