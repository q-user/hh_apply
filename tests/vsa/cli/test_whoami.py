"""Tests for the ``whoami`` VSA sub-command (issue #147, replaces legacy).

The ``whoami`` CLI op is a thin VSA adapter over the
:class:`ConfigAuthSlice.users` port. It:
  1. Reads the current user via the slice's ``UserPort``.
  2. Stores ``user.full_name``, ``user.email`` and ``user.phone`` in
     the storage settings table.
  3. Prints a single line with the user id, name, and counters.

These tests verify the ``Operation`` class shape (constructor-injected
slice, no more ``tool: HHApplicantTool`` arg), the argparse surface, and
the wiring of the print/set pipeline against an in-memory fake slice.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from job_bot.cli.whoami import Operation


class _FakeApiClient:
    """Minimal API client that returns a canned /me payload."""

    def __init__(self, me: dict[str, Any] | None = None) -> None:
        self._me = me or {
            "id": "u-1",
            "auth_type": "applicant",
            "first_name": "Ivan",
            "last_name": "Petrov",
            "middle_name": None,
            "email": "ivan@example.com",
            "phone": "+10000000",
            "counters": {
                "resumes_count": 3,
                "new_resume_views": 7,
                "unread_negotiations": 0,
            },
        }
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((endpoint, params))
        return self._me


class _FakeUserPort:
    """In-memory ``UserPort`` double."""

    def __init__(
        self,
        *,
        user_id: str = "u-1",
        auth_type: str = "applicant",
        first: str = "Ivan",
        last: str = "Petrov",
        email: str = "ivan@example.com",
        phone: str = "+10000000",
        resumes: int = 3,
        views: int = 7,
        unread: int = 0,
    ) -> None:
        self._payload: dict[str, Any] = {
            "id": user_id,
            "auth_type": auth_type,
            "first_name": first,
            "last_name": last,
            "middle_name": None,
            "email": email,
            "phone": phone,
            "counters": {
                "resumes_count": resumes,
                "new_resume_views": views,
                "unread_negotiations": unread,
            },
        }
        self.calls: list[str] = []

    def get_user(self, user_id: str) -> Any | None:
        self.calls.append(user_id)
        # The test exercises the case where the user_port is the source of
        # truth for user data (id may differ from the API client's ``/me``
        # id; e.g. multi-profile flows). Return the payload for any
        # non-empty id so the op can use it.
        if not user_id:
            return None
        return self._payload


class _FakeSettingsPort:
    """In-memory settings store: ``set_value`` is the only call we need."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def set_value(self, key: str, value: Any) -> None:
        self.values[key] = value

    def __enter__(self) -> "_FakeSettingsPort":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class _FakeSlice:
    """Minimal ``ConfigAuthSlice`` double with the ports ``whoami`` uses."""

    def __init__(
        self,
        user_port: _FakeUserPort | None = None,
        settings: _FakeSettingsPort | None = None,
        api_client: _FakeApiClient | None = None,
    ) -> None:
        self.users = user_port or _FakeUserPort()
        self.settings = settings or _FakeSettingsPort()
        self.api_client = api_client or _FakeApiClient()


def _make_args() -> argparse.Namespace:
    """Build a minimal argparse.Namespace for the whoami op."""
    return argparse.Namespace(profile_id="default", config_dir=None)


class TestWhoamiSetupParser:
    """The ``setup_parser`` surface."""

    def test_no_arguments(self) -> None:
        """``whoami`` takes no extra arguments."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd").add_parser("whoami")
        Operation().setup_parser(sub)
        ns = parser.parse_args(["whoami"])
        assert ns.cmd == "whoami"


class TestWhoamiRun:
    """The ``run(args) -> int`` contract."""

    def test_run_stores_user_data_in_settings(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``run()`` calls ``settings.set_value`` for name/email/phone."""
        users = _FakeUserPort(
            first="Ivan", last="Petrov", email="i@e.com", phone="+1"
        )
        settings = _FakeSettingsPort()
        op = Operation(slice_=_FakeSlice(user_port=users, settings=settings))

        rc = op.run(_make_args())

        assert rc == 0
        assert settings.values["user.full_name"] == "Petrov Ivan"
        assert settings.values["user.email"] == "i@e.com"
        assert settings.values["user.phone"] == "+1"

    def test_run_prints_user_id_and_counters(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``run()`` prints a one-line summary with id and counters."""
        users = _FakeUserPort(
            user_id="abc-123",
            first="Anna",
            last="Lee",
            resumes=5,
            views=12,
            unread=3,
        )
        op = Operation(slice_=_FakeSlice(user_port=users))

        rc = op.run(_make_args())

        out = capsys.readouterr().out
        assert rc == 0
        assert "abc-123" in out
        assert "Anna" in out or "Lee" in out
        # Counter formatting: resumes_count is absolute, views/unread as +N.
        assert "5" in out
        assert "+12" in out
        assert "+3" in out

    def test_run_warns_on_non_applicant_auth(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A non-applicant auth_type logs a warning."""
        users = _FakeUserPort(auth_type="employer")
        op = Operation(slice_=_FakeSlice(user_port=users))

        op.run(_make_args())

        assert any(
            "соискатель" in rec.message or "applicant" in rec.message.lower()
            for rec in caplog.records
        )

    def test_run_returns_1_when_api_returns_no_user_id(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``/me`` returning no id -> exit 1, no print, no persistence."""
        api = _FakeApiClient(me={"id": ""})
        settings = _FakeSettingsPort()
        op = Operation(slice_=_FakeSlice(settings=settings, api_client=api))

        rc = op.run(_make_args())

        assert rc == 1
        assert capsys.readouterr().out == ""
        # No user fields should have been written.
        assert settings.values == {}

    def test_run_uses_user_port_when_available(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``user_port.get_user(id)`` is consulted when the slice has one."""
        api = _FakeApiClient()
        users = _FakeUserPort()
        op = Operation(slice_=_FakeSlice(user_port=users, api_client=api))

        op.run(_make_args())

        # The user_port.get_user was called.
        assert users.calls == ["u-1"]


class TestWhoamiVsaShape:
    """The VSA constructor-injected contract."""

    def test_takes_slice_in_constructor(self) -> None:
        """``Operation(slice_=...)`` is the VSA DI contract."""
        op = Operation(slice_=_FakeSlice())
        # The op stashes the slice on a private attribute.
        assert op._slice is not None  # type: ignore[attr-defined]
