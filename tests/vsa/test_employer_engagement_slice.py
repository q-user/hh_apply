"""Tests for the ``employer_engagement`` VSA slice (issue #137).

The slice encapsulates the "reply to employers" workflow that used to
live in :mod:`hh_applicant_tool.operations.reply_employers`:

* iterate over open negotiations,
* pick the right reply mode (template / AI / interactive),
* honour period / only-invitations / dry-run filters,
* never call ``POST /negotiations/{id}/messages`` in dry-run mode.

Tests use in-memory fakes for the HTTP/AI boundaries and exercise the
slice via dependency-injection.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import pytest

# ─── Fakes ────────────────────────────────────────────────────────


class FakeApiError(Exception):
    """Replacement for the legacy :class:`ApiError` (HTTP error boundary)."""


@dataclass
class _BaseNegotiation:
    """Internal base — never yielded directly; tests use :class:`FakeNegotiation`
    which extends ``dict`` so it satisfies the slice's port protocol.
    """

    id: str
    state_id: str
    updated_at: str
    resume_id: str
    vacancy_name: str
    employer_id: str = "emp-1"
    employer_name: str = "Acme"
    employer_alternate_url: str = "https://hh.ru/employer/1"
    vacancy_alternate_url: str = "https://hh.ru/vacancy/1"
    viewed_by_opponent: bool = True


class FakeNegotiation(dict):
    """A minimal negotiation record used in tests.

    Mirrors the subset of fields the slice actually reads. Inherits
    from ``dict`` so the slice's ``NegotiationSourcePort`` accepts it
    transparently (the port's signature is
    ``Iterable[Invitation | dict[str, Any]]``).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        # Populate from a base dataclass for readability
        base = _BaseNegotiation(*args, **kwargs)
        self.update(
            {
                "id": base.id,
                "state": {"id": base.state_id},
                "updated_at": base.updated_at,
                "resume": {"id": base.resume_id},
                "vacancy": {
                    "name": base.vacancy_name,
                    "alternate_url": base.vacancy_alternate_url,
                    "employer": {
                        "id": base.employer_id,
                        "name": base.employer_name,
                        "alternate_url": base.employer_alternate_url,
                    },
                },
                "viewed_by_opponent": base.viewed_by_opponent,
            }
        )


@dataclass
class _BaseMessage:
    id: str
    text: str
    author_type: str
    created_at: str


class FakeMessage(dict):
    """A minimal message record used in tests.

    Inherits from ``dict`` so the slice's
    :class:`MessageSourcePort` accepts it transparently.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        base = _BaseMessage(*args, **kwargs)
        self.update(
            {
                "id": base.id,
                "text": base.text,
                "author": {"participant_type": base.author_type},
                "created_at": base.created_at,
            }
        )


@dataclass
class SentMessage:
    """Record of a single ``POST /negotiations/{id}/messages`` call."""

    negotiation_id: str
    text: str
    delay: float | None = None


@dataclass
class BlacklistedEmployer:
    """Record of a single ``PUT /employers/blacklisted/{id}`` call."""

    employer_id: str


class FakeHHApiClient:
    """In-memory replacement for :class:`HHApiClient`."""

    def __init__(
        self,
        *,
        negotiations: Iterable[FakeNegotiation] | None = None,
        messages_by_negotiation: dict[str, list[FakeMessage]] | None = None,
    ) -> None:
        self._negotiations = list(negotiations or [])
        self._messages = dict(messages_by_negotiation or {})
        self.sent_messages: list[SentMessage] = []
        self.blacklisted: list[BlacklistedEmployer] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.put_calls: list[tuple[str, dict[str, Any]]] = []
        self.delete_calls: list[tuple[str, dict[str, Any]]] = []
        # By default, the negotiations are accessible under the
        # ``status="active"`` query — legacy API surface.
        self.negotiation_status_filter: str | None = "active"

    # Negotiations listing ────────────────────────────────────────
    def iter_negotiations(
        self, status: str = "active"
    ) -> Iterable[FakeNegotiation]:
        if self.negotiation_status_filter is not None and (
            self.negotiation_status_filter != status
        ):
            return
        yield from self._negotiations

    # Messages listing ────────────────────────────────────────────
    def iter_messages(self, negotiation_id: str) -> Iterable[FakeMessage]:
        yield from self._messages.get(negotiation_id, [])

    # Mutation ────────────────────────────────────────────────────
    def post_message(
        self,
        negotiation_id: str,
        *,
        text: str,
        delay: float | None = None,
    ) -> None:
        self.post_calls.append(
            (
                f"/negotiations/{negotiation_id}/messages",
                {"message": text, "delay": delay},
            )
        )
        self.sent_messages.append(
            SentMessage(negotiation_id, text, delay=delay)
        )

    def blacklist_employer(self, employer_id: str) -> None:
        self.put_calls.append((f"/employers/blacklisted/{employer_id}", {}))
        self.blacklisted.append(BlacklistedEmployer(employer_id))


class FakeAIClient:
    """In-memory replacement for an OpenAI-style chat client."""

    def __init__(self, canned_text: str = "AI reply") -> None:
        self.canned_text = canned_text
        self.calls: list[dict[str, Any]] = []
        self.fail: bool = False

    def complete(self, query: str) -> str:
        self.calls.append({"query": query})
        if self.fail:
            raise RuntimeError("boom")
        return self.canned_text


# The slice depends on three small Protocols; we re-state them as
# concrete fakes so the test can wire them up by duck-typing.
class NegotiationSourcePort(Protocol):
    def iter_negotiations(
        self, status: str = ...
    ) -> Iterable[FakeNegotiation]: ...


class MessageSourcePort(Protocol):
    def iter_messages(self, negotiation_id: str) -> Iterable[FakeMessage]: ...


class EmployerActionsPort(Protocol):
    def post_message(
        self, negotiation_id: str, *, text: str, delay: float | None = ...
    ) -> None: ...
    def blacklist_employer(self, employer_id: str) -> None: ...


class AIClientPort(Protocol):
    def complete(self, query: str) -> str: ...


# ─── Helpers ──────────────────────────────────────────────────────


def make_negotiation(
    *,
    id: str = "n1",
    state_id: str = "invitation",
    days_ago: int = 0,
    resume_id: str = "r1",
    employer_id: str = "emp-1",
    vacancy_name: str = "Python Dev",
    viewed_by_opponent: bool = True,
) -> FakeNegotiation:
    now = dt.datetime.now(dt.timezone.utc)
    updated = now - dt.timedelta(days=days_ago)
    return FakeNegotiation(
        id=id,
        state_id=state_id,
        updated_at=updated.isoformat(),
        resume_id=resume_id,
        employer_id=employer_id,
        vacancy_name=vacancy_name,
        viewed_by_opponent=viewed_by_opponent,
    )


# ─── Tests ────────────────────────────────────────────────────────


class TestEmployerEngagementSliceImport:
    """The slice module must be importable and re-export the public API."""

    def test_slice_module_imports(self) -> None:
        from job_bot.employer_engagement import (
            EmployerEngagementSlice,
            create_employer_engagement_slice,
        )

        assert EmployerEngagementSlice is not None
        assert callable(create_employer_engagement_slice)


class TestEmployerEngagementSliceBasics:
    """Basic construction & wiring of the slice."""

    def test_create_slice_with_fakes(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        api = FakeHHApiClient()
        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=FakeAIClient(),
            resumes=[{"id": "r1", "title": "Pythonista"}],
        )
        assert slice_ is not None
        assert slice_.engagement is not None

    def test_factory_returns_slice(self) -> None:
        from job_bot.employer_engagement.slice import (
            create_employer_engagement_slice,
        )

        api = FakeHHApiClient()
        slice_ = create_employer_engagement_slice(api=api, ai_client=None)
        assert slice_.engagement is not None


class TestReplyModes:
    """Template / AI / interactive reply modes are routed correctly."""

    def test_template_reply_sends_message(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        neg = make_negotiation(
            id="n1",
            state_id="invitation",
            employer_id="emp-1",
        )
        api = FakeHHApiClient(negotiations=[neg])
        messages = [
            FakeMessage(
                id="m1",
                text="Приглашаем на собеседование",
                author_type="employer",
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        ]
        api._messages["n1"] = messages  # type: ignore[attr-defined]

        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=FakeAIClient(),
            resumes=[
                {
                    "id": "r1",
                    "title": "Senior Python",
                    "status": {"id": "published"},
                },
            ],
            reply_message="Здравствуйте, %(vacancy_name)s!",
            use_ai=False,
        )
        slice_.engagement.run(dry_run=False)

        assert len(api.sent_messages) == 1
        sent = api.sent_messages[0]
        assert sent.negotiation_id == "n1"
        # The template substitutes ``%(vacancy_name)s`` with the
        # negotiation's vacancy name ("Python Dev" from ``make_negotiation``).
        assert "Python Dev" in sent.text

    def test_ai_reply_uses_ai_client(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        neg = make_negotiation(
            id="n1",
            state_id="invitation",
            employer_id="emp-1",
        )
        api = FakeHHApiClient(negotiations=[neg])
        api._messages["n1"] = [  # type: ignore[attr-defined]
            FakeMessage(
                id="m1",
                text="Привет",
                author_type="employer",
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        ]

        ai = FakeAIClient(canned_text="AI-generated reply")
        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=ai,
            resumes=[
                {
                    "id": "r1",
                    "title": "Senior Python",
                    "status": {"id": "published"},
                },
            ],
            reply_message=None,
            use_ai=True,
        )
        slice_.engagement.run(dry_run=False)

        assert len(ai.calls) == 1
        assert len(api.sent_messages) == 1
        assert api.sent_messages[0].text == "AI-generated reply"

    def test_ai_failure_skips_negotiation(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        neg = make_negotiation(id="n1", state_id="invitation")
        api = FakeHHApiClient(negotiations=[neg])
        api._messages["n1"] = [  # type: ignore[attr-defined]
            FakeMessage(
                id="m1",
                text="Привет",
                author_type="employer",
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        ]
        ai = FakeAIClient(canned_text="won't get used")
        ai.fail = True

        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=ai,
            resumes=[
                {
                    "id": "r1",
                    "title": "Pythonista",
                    "status": {"id": "published"},
                },
            ],
            reply_message=None,
            use_ai=True,
        )
        slice_.engagement.run(dry_run=False)

        # AI failure → negotiation is skipped, no message posted.
        assert api.sent_messages == []

    def test_only_invitations_filter(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        inv = make_negotiation(id="n1", state_id="invitation")
        resp = make_negotiation(id="n2", state_id="response")
        api = FakeHHApiClient(negotiations=[inv, resp])
        # both have an employer message
        for nid in ("n1", "n2"):
            api._messages[nid] = [  # type: ignore[attr-defined]
                FakeMessage(
                    id="m1",
                    text="Привет",
                    author_type="employer",
                    created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                )
            ]
        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=None,
            resumes=[
                {
                    "id": "r1",
                    "title": "Pythonista",
                    "status": {"id": "published"},
                },
            ],
            reply_message="Hi %(vacancy_name)s",
            use_ai=False,
            only_invitations=True,
        )
        slice_.engagement.run(dry_run=False)
        sent_ids = {m.negotiation_id for m in api.sent_messages}
        assert sent_ids == {"n1"}


class TestDryRunAndFilters:
    """Dry-run, period filter, blacklist filter behaviour."""

    def test_dry_run_does_not_post(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        neg = make_negotiation(id="n1", state_id="invitation")
        api = FakeHHApiClient(negotiations=[neg])
        api._messages["n1"] = [  # type: ignore[attr-defined]
            FakeMessage(
                id="m1",
                text="Привет",
                author_type="employer",
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        ]
        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=None,
            resumes=[
                {
                    "id": "r1",
                    "title": "Pythonista",
                    "status": {"id": "published"},
                },
            ],
            reply_message="Hi %(vacancy_name)s",
            use_ai=False,
        )
        slice_.engagement.run(dry_run=True)
        assert api.sent_messages == []
        assert api.post_calls == []

    def test_period_filter_skips_old_negotiations(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        old = make_negotiation(id="old", state_id="invitation", days_ago=30)
        recent = make_negotiation(
            id="recent", state_id="invitation", days_ago=2
        )
        api = FakeHHApiClient(negotiations=[old, recent])
        for nid in ("old", "recent"):
            api._messages[nid] = [  # type: ignore[attr-defined]
                FakeMessage(
                    id="m1",
                    text="Привет",
                    author_type="employer",
                    created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                )
            ]
        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=None,
            resumes=[
                {
                    "id": "r1",
                    "title": "Pythonista",
                    "status": {"id": "published"},
                },
            ],
            reply_message="Hi %(vacancy_name)s",
            use_ai=False,
            period=7,
        )
        slice_.engagement.run(dry_run=False)
        sent_ids = {m.negotiation_id for m in api.sent_messages}
        assert sent_ids == {"recent"}

    def test_discarded_state_is_skipped(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        disc = make_negotiation(id="n1", state_id="discard")
        api = FakeHHApiClient(negotiations=[disc])
        api._messages["n1"] = [  # type: ignore[attr-defined]
            FakeMessage(
                id="m1",
                text="Привет",
                author_type="employer",
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        ]
        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=None,
            resumes=[
                {
                    "id": "r1",
                    "title": "Pythonista",
                    "status": {"id": "published"},
                },
            ],
            reply_message="Hi %(vacancy_name)s",
            use_ai=False,
        )
        slice_.engagement.run(dry_run=False)
        assert api.sent_messages == []

    def test_blacklisted_employer_is_skipped(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        neg = make_negotiation(
            id="n1", state_id="invitation", employer_id="emp-bad"
        )
        api = FakeHHApiClient(negotiations=[neg])
        api._messages["n1"] = [  # type: ignore[attr-defined]
            FakeMessage(
                id="m1",
                text="Привет",
                author_type="employer",
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        ]
        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=None,
            resumes=[
                {
                    "id": "r1",
                    "title": "Pythonista",
                    "status": {"id": "published"},
                },
            ],
            reply_message="Hi {vacancy_name}",
            use_ai=False,
            blacklisted_employers={"emp-bad"},
        )
        slice_.engagement.run(dry_run=False)
        assert api.sent_messages == []


class TestResumeFilter:
    """Only published resumes matching the filter are considered."""

    def test_unpublished_resume_is_skipped(self) -> None:
        from job_bot.employer_engagement.slice import (
            EmployerEngagementSlice,
        )

        neg = make_negotiation(id="n1", state_id="invitation", resume_id="r1")
        api = FakeHHApiClient(negotiations=[neg])
        api._messages["n1"] = [  # type: ignore[attr-defined]
            FakeMessage(
                id="m1",
                text="Привет",
                author_type="employer",
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        ]
        # Resume r1 is "hidden" — not published
        slice_ = EmployerEngagementSlice(
            api=api,
            ai_client=None,
            resumes=[{"id": "r1", "title": "Pythonista", "status": "hidden"}],
            reply_message="Hi %(vacancy_name)s",
            use_ai=False,
        )
        slice_.engagement.run(dry_run=False)
        assert api.sent_messages == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
