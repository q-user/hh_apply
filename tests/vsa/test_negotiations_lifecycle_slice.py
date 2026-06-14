"""Tests for the ``negotiations.lifecycle`` VSA slice (issue #137).

The slice encapsulates the "clear negotiations" workflow that used to
live in :mod:`hh_applicant_tool.operations.clear_negotiations`:

* iterate negotiations (active + discarded + refused),
* classify each by state / age / ATS signal / blacklist status,
* act: decline the negotiation, delete the chat, blacklist the employer,
* honour dry-run mode.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import pytest

# ─── Fakes ────────────────────────────────────────────────────────


class FakeApiError(Exception):
    """Replacement for the legacy :class:`ApiError`."""


@dataclass
class _BaseNegotiation:
    id: str
    state_id: str
    created_at: str
    updated_at: str
    employer_id: str | None
    employer_name: str = "Acme"
    employer_alternate_url: str = "https://hh.ru/employer/1"
    vacancy_name: str = "Python Dev"
    vacancy_alternate_url: str = "https://hh.ru/vacancy/1"


class FakeNegotiation(dict):
    """A minimal negotiation record used in tests.

    Inherits from ``dict`` so the slice's
    :class:`LifecycleApiPort` accepts it transparently.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        base = _BaseNegotiation(*args, **kwargs)
        self.update(
            {
                "id": base.id,
                "state": {"id": base.state_id},
                "created_at": base.created_at,
                "updated_at": base.updated_at,
                "vacancy": {
                    "name": base.vacancy_name,
                    "alternate_url": base.vacancy_alternate_url,
                    "employer": {
                        "id": base.employer_id,
                        "name": base.employer_name,
                        "alternate_url": base.employer_alternate_url,
                    },
                },
            }
        )


@dataclass
class DeclineCall:
    """Record of a single ``DELETE /negotiations/active/{id}`` call."""

    negotiation_id: str
    with_decline_message: bool


@dataclass
class BlacklistCall:
    employer_id: str


@dataclass
class ChatDeleteCall:
    """Record of a single chat-deletion (web-trash) call."""

    topic: int | str


class FakeApi:
    """In-memory replacement for the HH API."""

    def __init__(
        self,
        *,
        negotiations: Iterable[FakeNegotiation] | None = None,
    ) -> None:
        self._negotiations = list(negotiations or [])
        self.declines: list[DeclineCall] = []
        self.blacklists: list[BlacklistCall] = []
        self.chat_deletes: list[ChatDeleteCall] = []
        self.fail_decline: set[str] = set()
        self.fail_blacklist: set[str] = set()
        self.fail_chat_delete: set[str] = set()

    def iter_negotiations(
        self, status: str = "all"
    ) -> Iterable[FakeNegotiation]:
        yield from self._negotiations

    def decline_negotiation(
        self, negotiation_id: str, *, with_decline_message: bool
    ) -> None:
        if negotiation_id in self.fail_decline:
            raise FakeApiError("decline failed")
        self.declines.append(DeclineCall(negotiation_id, with_decline_message))

    def blacklist_employer(self, employer_id: str) -> None:
        if employer_id in self.fail_blacklist:
            raise FakeApiError("blacklist failed")
        self.blacklists.append(BlacklistCall(employer_id))

    def delete_chat(self, topic: int | str) -> bool:
        if topic in self.fail_chat_delete:
            return False
        self.chat_deletes.append(ChatDeleteCall(topic))
        return True


# ─── Protocols (also implemented by the slice) ───────────────────


class LifecycleApiPort(Protocol):
    def iter_negotiations(
        self, status: str = ...
    ) -> Iterable[FakeNegotiation]: ...
    def decline_negotiation(
        self, negotiation_id: str, *, with_decline_message: bool
    ) -> None: ...
    def blacklist_employer(self, employer_id: str) -> None: ...
    def delete_chat(self, topic: int | str) -> bool: ...


# ─── Helpers ──────────────────────────────────────────────────────


def make_negotiation(
    *,
    id: str = "n1",
    state_id: str = "discard",
    days_old: int = 0,
    days_since_response: int | None = None,
    employer_id: str | None = "emp-1",
    employer_name: str = "Acme",
) -> FakeNegotiation:
    now = dt.datetime.now(dt.timezone.utc)
    created = now - dt.timedelta(days=days_old)
    if days_since_response is None:
        updated = created
    else:
        updated = created + dt.timedelta(days=days_since_response)
    return FakeNegotiation(
        id=id,
        state_id=state_id,
        created_at=created.isoformat(),
        updated_at=updated.isoformat(),
        employer_id=employer_id,
        employer_name=employer_name,
    )


# ─── Tests ────────────────────────────────────────────────────────


class TestNegotiationsLifecycleSliceImport:
    """The slice module must be importable and re-export the public API."""

    def test_slice_module_imports(self) -> None:
        from job_bot.negotiations.lifecycle import (
            NegotiationLifecycleResult,
            NegotiationLifecycleSlice,
            create_negotiation_lifecycle_slice,
        )

        assert NegotiationLifecycleSlice is not None
        assert callable(create_negotiation_lifecycle_slice)
        assert NegotiationLifecycleResult is not None

    def test_parent_negotiations_package_re_exports(self) -> None:
        from job_bot.negotiations import (
            NegotiationsSlice,
            create_negotiations_slice,
        )

        assert NegotiationsSlice is not None
        assert callable(create_negotiations_slice)


class TestNegotiationsLifecycleBasics:
    """Basic construction & wiring of the slice."""

    def test_create_slice(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        api = FakeApi()
        slice_ = NegotiationLifecycleSlice(api=api)
        assert slice_ is not None
        assert slice_.api is api

    def test_factory_returns_slice(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            create_negotiation_lifecycle_slice,
        )

        api = FakeApi()
        slice_ = create_negotiation_lifecycle_slice(api=api)
        assert slice_.api is api


class TestParentSliceExposesSubSlice:
    """The parent ``NegotiationsSlice`` exposes ``lifecycle`` as a property."""

    def test_parent_lifecycle_property(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )
        from job_bot.negotiations.slice import NegotiationsSlice

        api = FakeApi()
        parent = NegotiationsSlice(api=api)
        assert isinstance(parent.lifecycle, NegotiationLifecycleSlice)
        assert parent.lifecycle.api is api


class TestDiscardStateMachine:
    """The default mode (no flags) declines ``discard`` and ``refusal``."""

    def test_default_mode_declines_discarded(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        neg = make_negotiation(id="n1", state_id="discard")
        api = FakeApi(negotiations=[neg])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run()

        assert result.declined == 1
        assert len(api.declines) == 1
        assert api.declines[0].negotiation_id == "n1"
        # ``with_decline_message`` is False for ``discard``
        assert api.declines[0].with_decline_message is False

    def test_default_mode_declines_refusal(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        neg = make_negotiation(id="n1", state_id="refusal")
        api = FakeApi(negotiations=[neg])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run()

        assert result.declined == 1
        assert api.declines[0].with_decline_message is True

    def test_default_mode_skips_active(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        active = make_negotiation(id="n1", state_id="invitation")
        api = FakeApi(negotiations=[active])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run()
        assert result.declined == 0
        assert api.declines == []


class TestOlderThanFilter:
    """``--older-than N`` declines anything updated more than N days ago."""

    def test_older_than_includes_active(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        # Active but updated 30 days ago → still declined with --older-than
        old_active = make_negotiation(
            id="n1", state_id="invitation", days_old=30
        )
        api = FakeApi(negotiations=[old_active])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run(older_than=7)
        assert result.declined == 1

    def test_older_than_skips_recent(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        recent = make_negotiation(id="n1", state_id="invitation", days_old=2)
        api = FakeApi(negotiations=[recent])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run(older_than=7)
        assert result.declined == 0


class TestATSDetection:
    """ATS detection fires when the response was received quickly."""

    def test_ats_detected_on_fast_response(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        # Responded in 5 minutes (< 16 minutes threshold)
        neg = make_negotiation(
            id="n1",
            state_id="refusal",
            days_old=1,
            days_since_response=0,
        )
        # Override updated_at to be ~5 minutes after created_at
        created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        updated = created + dt.timedelta(minutes=5)
        neg["created_at"] = created.isoformat()
        neg["updated_at"] = updated.isoformat()

        api = FakeApi(negotiations=[neg])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run(block_ats=True)
        assert result.blacklisted == 1
        assert result.ats_detected == 1

    def test_ats_not_detected_on_slow_response(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        # Responded in 1 hour (> 16 minutes threshold)
        created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
        updated = created + dt.timedelta(hours=1)
        neg = make_negotiation(id="n1", state_id="refusal")
        neg["created_at"] = created.isoformat()
        neg["updated_at"] = updated.isoformat()

        api = FakeApi(negotiations=[neg])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run(block_ats=True)
        assert result.ats_detected == 0
        assert result.blacklisted == 0


class TestBlacklistAndChatDelete:
    """``--blacklist-discard`` and ``--delete-chat`` flags behave correctly."""

    def test_blacklist_discard(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        neg = make_negotiation(
            id="n1", state_id="discard", employer_id="emp-bad"
        )
        api = FakeApi(negotiations=[neg])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run(blacklist_discard=True)
        assert result.blacklisted == 1
        assert api.blacklists[0].employer_id == "emp-bad"

    def test_blacklist_skips_already_blacklisted(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        neg = make_negotiation(
            id="n1", state_id="discard", employer_id="emp-bad"
        )
        api = FakeApi(negotiations=[neg])
        slice_ = NegotiationLifecycleSlice(
            api=api, blacklisted_employers={"emp-bad"}
        )
        result = slice_.run(blacklist_discard=True)
        assert result.blacklisted == 0
        assert api.blacklists == []

    def test_blacklist_skips_employer_without_id(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        # Anonymized employer (id is None) — must not crash
        neg = make_negotiation(id="n1", state_id="discard", employer_id=None)
        api = FakeApi(negotiations=[neg])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run(blacklist_discard=True)
        assert result.blacklisted == 0

    def test_delete_chat_flag(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        neg = make_negotiation(id="42", state_id="discard")
        api = FakeApi(negotiations=[neg])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run(delete_chat=True)
        assert result.chats_deleted == 1
        assert len(api.chat_deletes) == 1
        assert api.chat_deletes[0].topic == "42"


class TestDryRun:
    """``--dry-run`` never calls the mutating endpoints."""

    def test_dry_run_skips_all_mutations(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        neg = make_negotiation(id="n1", state_id="discard")
        api = FakeApi(negotiations=[neg])
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run(
            blacklist_discard=True, delete_chat=True, dry_run=True
        )
        assert result.declined == 1  # counted
        assert result.blacklisted == 1  # counted
        assert result.chats_deleted == 1  # counted
        assert api.declines == []
        assert api.blacklists == []
        assert api.chat_deletes == []


class TestResilience:
    """Errors from the API don't kill the whole run."""

    def test_decline_error_continues_with_next(self) -> None:
        from job_bot.negotiations.lifecycle.slice import (
            NegotiationLifecycleSlice,
        )

        n1 = make_negotiation(id="n1", state_id="discard")
        n2 = make_negotiation(id="n2", state_id="discard")
        api = FakeApi(negotiations=[n1, n2])
        api.fail_decline.add("n1")
        slice_ = NegotiationLifecycleSlice(api=api)
        result = slice_.run()
        # n1 fails, n2 succeeds
        assert result.declined == 1
        assert len(api.declines) == 1
        assert api.declines[0].negotiation_id == "n2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
