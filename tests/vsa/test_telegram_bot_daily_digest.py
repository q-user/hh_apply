"""Bridge tests for the daily-digest VSA migration (issue #8).

The legacy module :mod:`hh_applicant_tool.services.daily_digest` (413 LOCs)
is being migrated into the VSA ``telegram_bot`` slice as
:mod:`job_bot.telegram_bot.services.daily_digest_service`. These tests
cover the new VSA module's public surface end-to-end on an in-memory
SQLite database with a mocked :class:`TelegramTransport`.

Three groups:

* :class:`TestDailyDigestServiceModule` — the new VSA module exports
  the public API (service + DTOs + constants).
* :class:`TestDailyDigestServiceBehaviour` — the service behaves as
  documented: groups ``prepared`` drafts, formats the message, sends it
  via the transport, honours the same-day idempotency flag, and
  respects ``force=True``.
* :class:`TestDailyDigestServiceWiring` — the
  :class:`TelegramBotSlice` builds and exposes a
  :class:`DailyDigestService` end-to-end.

The deprecation warning contract for the legacy shim is enforced
centrally in :mod:`tests.test_issue_92_deprecation` (parametrised over
:data:`SHIM_CONTRACT`); here we only assert the *behavioural* surface
of the new VSA module and the slice wiring.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from job_bot.telegram_bot.telegram_transport import (
    TelegramTransport,
    TelegramTransportError,
)


# ─── Fixtures shared across the three groups ────────────────────────


CHAT_ID = 42


class _FixedClock:
    """Deterministic clock that satisfies the canonical ``Clock`` port.

    Implements :class:`hh_applicant_tool.application.ports.Clock` (both
    ``now`` and ``sleep``). ``sleep`` is a no-op — tests do not need
    real delays.
    """

    def __init__(self, day: date) -> None:
        self._now = datetime(day.year, day.month, day.day, 9, 0, 0)

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        return None


@pytest.fixture
def transport() -> MagicMock:
    """Mocked :class:`TelegramTransport` with a default ``send_message``."""
    t = MagicMock(spec=TelegramTransport)
    t.send_message.return_value = {"message_id": 1, "ok": True}
    return t


def _make_service(
    conn: sqlite3.Connection,
    *,
    transport: MagicMock | None = None,
    config: dict | None = None,
    day: date = date(2026, 6, 9),
    ai_client: MagicMock | None = None,
):
    """Build a :class:`DailyDigestService` with sensible defaults.

    Uses a fixed clock at ``day`` (9:00 AM) so tests are deterministic
    regardless of which calendar day they run on. The :class:`StorageFacade`
    wrap is done here so callers can pass the raw ``sqlite3.Connection``
    from the project-level ``storage`` fixture.
    """
    from hh_applicant_tool.storage import StorageFacade
    from job_bot.telegram_bot.services.daily_digest_service import (
        DailyDigestService,
    )

    facade = StorageFacade(conn)
    if transport is None:
        transport = MagicMock(spec=TelegramTransport)
        transport.send_message.return_value = {"message_id": 1, "ok": True}
    if config is None:
        config = {"telegram": {"digest_chat_id": CHAT_ID}}
    return DailyDigestService(
        storage=facade,
        transport=transport,
        config=config,
        clock=_FixedClock(day),
        ai_client=ai_client,
    )


def _save_profile(facade, pid: str, name: str, enabled: bool = True) -> None:
    from hh_applicant_tool.storage.models.search_profile import (
        SearchProfileModel,
    )

    facade.search_profiles.save(
        SearchProfileModel(id=pid, name=name, resume_id="r1", enabled=enabled)
    )


def _save_draft(
    facade,
    *,
    profile_id: str | None,
    resume_id: str,
    vacancy_id: int,
    status: str = "prepared",
    has_test: bool = False,
    relevance_score: int | None = None,
) -> None:
    from hh_applicant_tool.storage.models.application_draft import (
        ApplicationDraftModel,
    )

    facade.application_drafts.save(
        ApplicationDraftModel(
            search_profile_id=profile_id,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            status=status,
            has_test=has_test,
            relevance_score=relevance_score,
        )
    )


# ─── 1. New VSA module surface ─────────────────────────────────────


class TestDailyDigestServiceModule:
    """``job_bot.telegram_bot.services.daily_digest_service`` exports the
    full daily-digest public surface (issue #8)."""

    def test_module_is_importable(self) -> None:
        """The VSA module is on the import path and exposes the public
        ``DailyDigestService`` class."""
        from job_bot.telegram_bot.services import daily_digest_service

        assert daily_digest_service is not None
        assert hasattr(daily_digest_service, "DailyDigestService")

    def test_service_class_exported(self) -> None:
        """``DailyDigestService`` is a class with the public entry points
        (``send``, ``collect_groups``, ``format_message``,
        ``already_sent_today``, ``today``, ``clock``)."""
        from job_bot.telegram_bot.services.daily_digest_service import (
            DailyDigestService,
        )

        assert isinstance(DailyDigestService, type)
        for entry in (
            "send",
            "collect_groups",
            "format_message",
            "already_sent_today",
            "today",
        ):
            assert callable(getattr(DailyDigestService, entry)), (
                f"DailyDigestService.{entry} must be callable"
            )
        # ``clock`` is a property; verify it's a descriptor.
        assert isinstance(
            DailyDigestService.__dict__["clock"], property
        ), "clock must be a @property"

    def test_dto_classes_and_constant_exported(self) -> None:
        """``DraftGroup`` and ``DigestResult`` DTOs plus the
        ``LAST_DIGEST_KEY`` constant are exported from the VSA module."""
        from job_bot.telegram_bot.services import daily_digest_service

        for name in ("DraftGroup", "DigestResult", "LAST_DIGEST_KEY"):
            assert hasattr(daily_digest_service, name), (
                f"daily_digest_service must export {name}"
            )
        assert daily_digest_service.LAST_DIGEST_KEY == "telegram.last_digest_date"

    def test_dto_models_are_constructable(self) -> None:
        """``DraftGroup`` and ``DigestResult`` are constructable dataclasses
        that round-trip their fields (smoke-test for the public DTO
        surface)."""
        from job_bot.telegram_bot.services.daily_digest_service import (
            DigestResult,
            DraftGroup,
        )

        group = DraftGroup(
            search_profile_id="p1",
            profile_name="Profile 1",
            total=3,
            with_tests=1,
            without_tests=2,
            average_score=80,
        )
        assert group.search_profile_id == "p1"
        assert group.total == 3
        assert group.average_score == 80

        result = DigestResult(
            sent=True,
            total_drafts=3,
            message="hello",
        )
        assert result.sent is True
        assert result.skipped_reason is None
        assert result.total_drafts == 3
        assert result.message == "hello"


# ─── 2. Service behaviour ──────────────────────────────────────────


class TestDailyDigestServiceBehaviour:
    """``DailyDigestService`` reads ``application_drafts``, formats the
    message, sends it via the transport, and respects idempotency."""

    def test_service_constructible_with_minimal_args(
        self, storage: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """Minimal constructor: storage + transport only; config/clock/
        ai_client fall back to defaults (``SystemClock``, no AI)."""
        from hh_applicant_tool.storage import StorageFacade
        from job_bot.telegram_bot.services.daily_digest_service import (
            DailyDigestService,
        )

        svc = DailyDigestService(
            storage=StorageFacade(storage), transport=transport
        )
        assert svc.clock is not None  # fallback SystemClock

    def test_collect_groups_reads_prepared_drafts_only(
        self, storage: sqlite3.Connection
    ) -> None:
        """Only ``status='prepared'`` rows are aggregated; other statuses
        are filtered out by the WHERE clause."""
        from hh_applicant_tool.storage import StorageFacade

        facade = StorageFacade(storage)
        _save_profile(facade, "p1", "Profile 1")
        _save_draft(
            facade, profile_id="p1", resume_id="r1", vacancy_id=1,
            status="prepared",
        )
        _save_draft(
            facade, profile_id="p1", resume_id="r2", vacancy_id=2,
            status="rejected",
        )
        _save_draft(
            facade, profile_id="p1", resume_id="r3", vacancy_id=3,
            status="approved",
        )
        storage.commit()

        svc = _make_service(storage)
        groups = svc.collect_groups()
        assert len(groups) == 1
        assert groups[0].total == 1

    def test_collect_groups_groups_by_profile_and_splits_tests(
        self, storage: sqlite3.Connection
    ) -> None:
        """Grouping by ``search_profile_id`` and splitting by
        ``has_test`` produces the expected per-group counts and the
        rounded average ``relevance_score``."""
        from hh_applicant_tool.storage import StorageFacade

        facade = StorageFacade(storage)
        _save_profile(facade, "django", "Django Senior")
        _save_profile(facade, "fastapi", "FastAPI")

        # Django: 3 total (1 with test, 2 without)
        _save_draft(
            facade, profile_id="django", resume_id="r1", vacancy_id=10,
            has_test=True, relevance_score=90,
        )
        _save_draft(
            facade, profile_id="django", resume_id="r1", vacancy_id=11,
            has_test=False, relevance_score=80,
        )
        _save_draft(
            facade, profile_id="django", resume_id="r1", vacancy_id=12,
            has_test=False, relevance_score=70,
        )
        # FastAPI: 1 without test
        _save_draft(
            facade, profile_id="fastapi", resume_id="r2", vacancy_id=20,
            has_test=False, relevance_score=60,
        )
        storage.commit()

        groups = _make_service(storage).collect_groups()
        by_pid = {g.search_profile_id: g for g in groups}

        django = by_pid["django"]
        assert django.total == 3
        assert django.with_tests == 1
        assert django.without_tests == 2
        assert django.average_score == 80  # (90+80+70)/3
        assert django.profile_name == "Django Senior"

        fastapi = by_pid["fastapi"]
        assert fastapi.total == 1
        assert fastapi.with_tests == 0
        assert fastapi.without_tests == 1
        assert fastapi.average_score == 60
        assert fastapi.profile_name == "FastAPI"

    def test_send_calls_transport_with_expected_message(
        self, storage: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """``send`` invokes ``transport.send_message`` with the resolved
        ``chat_id`` and a Russian-language body listing the prepared
        drafts (with the expected header / footer markers)."""
        from hh_applicant_tool.storage import StorageFacade

        facade = StorageFacade(storage)
        _save_profile(facade, "p1", "Profile 1")
        _save_draft(
            facade, profile_id="p1", resume_id="r1", vacancy_id=1,
            relevance_score=75,
        )
        _save_draft(
            facade, profile_id="p1", resume_id="r1", vacancy_id=2,
            relevance_score=85,
        )
        storage.commit()

        svc = _make_service(storage, transport=transport)
        result = svc.send()

        assert result.sent is True
        assert result.skipped_reason is None
        assert result.total_drafts == 2
        # ``transport.send_message`` was called exactly once with the
        # resolved chat_id and a non-empty body.
        assert transport.send_message.call_count == 1
        call = transport.send_message.call_args
        # Positional or keyword — accept both.
        if call.args:
            chat_id, message = call.args
        else:
            chat_id = call.kwargs.get("chat_id")
            message = call.kwargs.get("message") or call.kwargs.get("text")
        assert chat_id == CHAT_ID
        assert "Доброе утро" in message
        assert "Готово к ревью: 2 вакансий" in message
        assert "Profile 1" in message
        assert "средний score: 80" in message

    def test_send_idempotent_same_day_is_no_op(
        self, storage: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """A second ``send`` on the same calendar date is a no-op:
        transport is *not* called and the result reports the
        ``already_sent`` skip reason. The first call marks today in
        ``settings.telegram.last_digest_date``."""
        svc = _make_service(storage, transport=transport)
        first = svc.send()
        assert first.sent is True

        # Second call same day → no-op.
        second = svc.send()
        assert second.sent is False
        assert second.skipped_reason == "already_sent"
        # Transport is still only called once.
        assert transport.send_message.call_count == 1

    def test_send_force_true_overrides_idempotency(
        self, storage: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """``force=True`` re-sends even when the same-day flag is set."""
        svc = _make_service(storage, transport=transport)
        first = svc.send()
        assert first.sent is True

        second = svc.send(force=True)
        assert second.sent is True
        # Transport was called again.
        assert transport.send_message.call_count == 2

    def test_send_skip_when_no_telegram_config(
        self, storage: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """A config without a ``telegram`` section is reported as
        ``no_telegram_config``; the transport is not touched."""
        svc = _make_service(storage, transport=transport, config={})
        result = svc.send()
        assert result.sent is False
        assert result.skipped_reason == "no_telegram_config"
        transport.send_message.assert_not_called()

    def test_send_skip_when_no_chat_id(
        self, storage: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """A telegram config without ``digest_chat_id`` / ``chat_id``
        and without ``allowed_user_ids`` is reported as ``no_chat_id``."""
        svc = _make_service(
            storage, transport=transport,
            config={"telegram": {}},  # no chat_id keys
        )
        result = svc.send()
        assert result.sent is False
        assert result.skipped_reason == "no_chat_id"
        transport.send_message.assert_not_called()

    def test_send_reports_send_failure_without_marking_sent(
        self, storage: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """When ``transport.send_message`` raises
        :class:`TelegramTransportError`, ``send`` returns
        ``send_failed`` and does *not* mark the day as sent (so the
        next call can retry)."""
        transport.send_message.side_effect = TelegramTransportError("boom")
        svc = _make_service(storage, transport=transport)

        first = svc.send()
        assert first.sent is False
        assert first.skipped_reason == "send_failed"
        assert first.total_drafts == 0  # empty DB
        assert first.message  # rendered body is still returned for logging

        # Idempotency flag was *not* set → a retry should be attempted,
        # not short-circuited as ``already_sent``.
        transport.send_message.side_effect = None
        transport.send_message.return_value = {"message_id": 2, "ok": True}
        second = svc.send()
        assert second.sent is True
        assert transport.send_message.call_count == 2

    def test_format_message_with_empty_groups(self) -> None:
        """``format_message`` with an empty group list produces a
        Russian-language «no drafts» body."""
        from job_bot.telegram_bot.services.daily_digest_service import (
            DailyDigestService,
        )

        body = DailyDigestService.format_message(groups=[], total=0)
        assert "Доброе утро" in body
        assert "нет подготовленных черновиков" in body


# ─── 3. Slice wiring ──────────────────────────────────────────────


class TestDailyDigestServiceWiring:
    """``TelegramBotSlice._default_digest_service`` builds a
    :class:`DailyDigestService` and exposes it through the
    :class:`DailyDigestPort` protocol (issue #8)."""

    def test_default_digest_service_factory_builds_service(
        self, storage: sqlite3.Connection
    ) -> None:
        """The module-level ``_default_digest_service`` factory returns
        a :class:`DailyDigestService` when given a raw
        :class:`sqlite3.Connection` (the slice resolves
        ``database`` → raw connection before calling the factory, so
        the factory does the ``StorageFacade`` wrap itself)."""
        from job_bot.telegram_bot.services.daily_digest_service import (
            DailyDigestService,
        )
        from job_bot.telegram_bot.slice import _default_digest_service

        transport = MagicMock(spec=TelegramTransport)
        service = _default_digest_service(
            storage=storage,
            transport=transport,
            config={"telegram": {"digest_chat_id": CHAT_ID}},
        )
        assert isinstance(service, DailyDigestService)
        # The service has its entry points wired up.
        assert callable(service.send)
        assert callable(service.collect_groups)

    def test_slice_digest_property_returns_digest_port(
        self, storage: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """``TelegramBotSlice.digest`` returns the configured digest
        service, structurally satisfying :class:`DailyDigestPort`."""
        from job_bot.telegram_bot.slice import TelegramBotSlice

        slice_ = TelegramBotSlice(
            database=storage,  # raw sqlite3.Connection
            transport=transport,
            config={"telegram": {"digest_chat_id": CHAT_ID}},
        )
        try:
            digest = slice_.digest
            # Duck-typed surface used by the slice's DigestHandler.
            assert hasattr(digest, "send")
            assert callable(digest.send)
        finally:
            slice_.close()

    def test_slice_accepts_custom_digest_service_override(
        self, storage: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """The slice honours a caller-supplied ``digest_service`` kwarg
        (lets tests inject a mock without going through the default
        factory)."""
        from job_bot.telegram_bot.slice import TelegramBotSlice

        custom = MagicMock()
        slice_ = TelegramBotSlice(
            database=storage,
            transport=transport,
            config={"telegram": {"digest_chat_id": CHAT_ID}},
            digest_service=custom,
        )
        try:
            assert slice_.digest is custom
        finally:
            slice_.close()


__all__ = (
    "TestDailyDigestServiceBehaviour",
    "TestDailyDigestServiceModule",
    "TestDailyDigestServiceWiring",
)
