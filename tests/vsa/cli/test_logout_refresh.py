"""Tests for the ``logout`` and ``refresh_token`` VSA sub-commands
(issue #147).

Both ops are thin VSA adapters over the :class:`ConfigAuthSlice.auth`
port's OAuth credentials storage.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from job_bot.cli.logout import Operation as LogoutOp
from job_bot.cli.refresh_token import Operation as RefreshTokenOp
from job_bot.config_auth.models.credentials import OAuthCredentials


class _FakeAuthPort:
    """In-memory auth port with call-recording."""

    def __init__(
        self,
        *,
        is_expired: bool = True,
        access_token: str = "old-access",
        refresh_token: str = "old-refresh",
        expires_at: int = 0,
    ) -> None:
        self._is_expired = is_expired
        self._creds = OAuthCredentials(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=expires_at,
        )
        self.refresh_calls: list[str] = []
        self.saved_calls: list[OAuthCredentials] = []
        self.delete_calls: list[str] = []

    def is_access_expired(self) -> bool:
        return self._is_expired

    def refresh_access_token(self) -> OAuthCredentials:
        self.refresh_calls.append("called")
        self._creds = OAuthCredentials(
            access_token="new-access",
            refresh_token="new-refresh",
            access_expires_at=10**12,
        )
        return self._creds

    def save_credentials(self, creds: OAuthCredentials) -> bool:
        self.saved_calls.append(creds)
        return True

    def get_credentials(self) -> OAuthCredentials | None:
        return self._creds

    def delete(self, endpoint: str) -> dict[str, Any]:
        self.delete_calls.append(endpoint)
        return {}


class _FakeApiClient:
    def __init__(self, delete_response: Any = None) -> None:
        self.deletes: list[str] = []
        self.delete_response = (
            delete_response if delete_response is not None else {}
        )
        self._auth = self.delete_response

    def delete(self, endpoint: str) -> dict[str, Any]:
        self.deletes.append(endpoint)
        return self.delete_response


class _FakeSlice:
    def __init__(
        self,
        auth: _FakeAuthPort | None = None,
        api_client: _FakeApiClient | None = None,
    ) -> None:
        self.auth = auth or _FakeAuthPort()
        self.api_client = api_client or _FakeApiClient()


def _make_parser(op_cls: type, name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser(name)
    op_cls().setup_parser(sub)
    return parser


class TestLogout:
    def test_setup_parser_no_args(self) -> None:
        parser = _make_parser(LogoutOp, "logout")
        ns = parser.parse_args(["logout"])
        assert ns.cmd == "logout"

    def test_run_calls_api_delete(self) -> None:
        api = _FakeApiClient()
        op = LogoutOp(slice_=_FakeSlice(api_client=api))

        parser = _make_parser(LogoutOp, "logout")
        ns = parser.parse_args(["logout"])
        rc = op.run(ns)

        assert rc == 0
        assert api.deletes == ["/oauth/token"]


class TestRefreshToken:
    def test_setup_parser_no_args(self) -> None:
        parser = _make_parser(RefreshTokenOp, "refresh-token")
        ns = parser.parse_args(["refresh-token"])
        assert ns.cmd == "refresh-token"

    def test_expired_token_is_refreshed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        auth = _FakeAuthPort(is_expired=True)
        op = RefreshTokenOp(slice_=_FakeSlice(auth=auth))

        parser = _make_parser(RefreshTokenOp, "refresh-token")
        ns = parser.parse_args(["refresh-token"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        assert auth.refresh_calls == ["called"]
        assert auth.saved_calls  # The new credentials were saved.
        assert "✅" in out or "успешно" in out.lower()

    def test_fresh_token_returns_2(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        auth = _FakeAuthPort(is_expired=False)
        op = RefreshTokenOp(slice_=_FakeSlice(auth=auth))

        parser = _make_parser(RefreshTokenOp, "refresh-token")
        ns = parser.parse_args(["refresh-token"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 2
        assert auth.refresh_calls == []  # No refresh attempted.
        assert "ℹ" in out or "истек" in out.lower()
