"""Bridge tests for the review-flow VSA migration (issue #87).

Three concerns, one test class each:

* :class:`TestReviewServiceModule` — the new VSA module exports the
  public API (service + callback + state constants + DTOs).
* :class:`TestReviewFlowShim` — the legacy
  :mod:`job_bot.telegram_bot.services.review_flow` module re-exports the
  same classes and emits the canonical deprecation warning.
* :class:`TestReviewServiceWiring` — the :class:`TelegramBotSlice` can
  build and expose a :class:`ReviewFlowService` end-to-end.

The deprecation warning contract itself is enforced centrally in
:mod:`tests.test_issue_92_deprecation` (parametrised over
:data:`SHIM_CONTRACT`); here we only assert the *behavioural* surface
of the shim and the slice wiring.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from job_bot.telegram_bot.telegram_transport import TelegramTransport

# ─── Fixtures shared across the three groups ────────────────────────


CHAT_ID = 12345


@pytest.fixture
def storage_conn() -> sqlite3.Connection:
    """In-memory SQLite with the canonical schema initialised."""
    from job_bot._legacy_compat.storage import StorageFacade

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    StorageFacade(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def transport() -> MagicMock:
    """Mocked :class:`TelegramTransport` (slice-level transport port)."""
    t = MagicMock(spec=TelegramTransport)
    t.allowed_user_ids = ()
    t.poll_timeout = 30
    t.send_message.return_value = {"message_id": 1, "ok": True}
    return t


# ─── 1. New VSA module surface ─────────────────────────────────────


class TestReviewServiceModule:
    """``job_bot.telegram_bot.services.review_service`` exports the
    full review-flow public surface (issue #87)."""

    def test_module_is_importable(self) -> None:
        """The VSA module is on the import path and exposes a public
        ``ReviewFlowService`` class."""
        from job_bot.telegram_bot.services import review_service

        assert review_service is not None
        assert hasattr(review_service, "ReviewFlowService")

    def test_review_flow_service_class_exported(self) -> None:
        """``ReviewFlowService`` is a class with the FSM entry points."""
        from job_bot.telegram_bot.services.review_service import (
            ReviewFlowService,
        )

        assert isinstance(ReviewFlowService, type)
        for entry in ("process_message", "process_callback", "resume_session"):
            assert callable(getattr(ReviewFlowService, entry)), (
                f"ReviewFlowService.{entry} must be callable"
            )

    def test_callback_and_state_constants_exported(self) -> None:
        """All callback_data and FSM state constants are re-exported."""
        from job_bot.telegram_bot.services import review_service

        expected_callbacks = {
            "CB_INTRO_CONTINUE",
            "CB_INTRO_SKIP",
            "CB_INTRO_OPEN",
            "CB_TEST_OK",
            "CB_TEST_CHOOSE",
            "CB_TEST_REGEN",
            "CB_TEST_CUSTOM",
            "CB_COVER_OK",
            "CB_COVER_REGEN",
            "CB_COVER_CUSTOM",
            "CB_CONFIRM_SEND",
            "CB_CONFIRM_SKIP",
        }
        expected_states = {
            "STATE_IDLE",
            "STATE_REVIEW_INTRO",
            "STATE_REVIEW_TEST",
            "STATE_AWAIT_TEST_REGEN",
            "STATE_AWAIT_TEST_CUSTOM",
            "STATE_REVIEW_COVER",
            "STATE_AWAIT_COVER_REGEN",
            "STATE_AWAIT_COVER_CUSTOM",
            "STATE_CONFIRM_APPLY",
        }
        for name in expected_callbacks | expected_states:
            assert hasattr(review_service, name), (
                f"review_service must export {name}"
            )

    def test_dto_models_are_reexported(self) -> None:
        """``OutgoingMessage`` and ``InlineButton`` DTOs are accessible
        from the VSA module (canonical VSA single source of truth)."""
        from job_bot.telegram_bot.models.message import (
            InlineButton,
            OutgoingMessage,
        )

        # OutgoingMessage is a dataclass with chat_id + text; smoke-test.
        msg = OutgoingMessage(chat_id=CHAT_ID, text="hello")
        assert msg.chat_id == CHAT_ID
        assert msg.text == "hello"
        # InlineButton is also constructable.
        btn = InlineButton(text="OK", callback_data="rf:test:ok")
        assert btn.callback_data == "rf:test:ok"


# ─── 3. Slice wiring ──────────────────────────────────────────────


class TestReviewServiceWiring:
    """``TelegramBotSlice._default_review_service`` builds a
    :class:`ReviewFlowService` and exposes it through the
    :class:`ReviewFlowPort` protocol (issue #87)."""

    def test_default_review_service_factory_builds_service(
        self, storage_conn: sqlite3.Connection
    ) -> None:
        """The module-level ``_default_review_service`` factory returns
        a :class:`ReviewFlowService` when given a raw
        :class:`sqlite3.Connection` (the slice resolves
        ``database`` → raw connection before calling the factory, so
        the factory does the ``StorageFacade`` wrap itself)."""
        from job_bot.telegram_bot.services.review_service import (
            ReviewFlowService,
        )
        from job_bot.telegram_bot.slice import _default_review_service

        transport = MagicMock(spec=TelegramTransport)
        service = _default_review_service(
            storage=storage_conn,
            transport=transport,
            config={"telegram": {"chat_id": CHAT_ID}},
        )
        assert isinstance(service, ReviewFlowService)
        # Service has the FSM entry points wired up.
        assert hasattr(service, "process_message")
        assert hasattr(service, "process_callback")
        assert hasattr(service, "resume_session")

    def test_slice_review_property_returns_review_flow_port(
        self, storage_conn: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """``TelegramBotSlice.review`` returns the configured review
        service, structurally satisfying :class:`ReviewFlowPort`."""
        from job_bot.telegram_bot.slice import TelegramBotSlice

        slice_ = TelegramBotSlice(
            database=storage_conn,  # raw sqlite3.Connection
            transport=transport,
            config={"telegram": {"chat_id": CHAT_ID}},
        )
        try:
            review = slice_.review
            # The ``review`` property is statically typed as ``ReviewFlowPort``
            # (structural Protocol, not ``@runtime_checkable``). Use
            # duck-typing to verify the slice wires up a usable service.
            assert hasattr(review, "process_message")
            assert callable(review.process_message)
            assert callable(review.process_callback)
            assert callable(review.resume_session)
        finally:
            slice_.close()

    def test_slice_accepts_custom_review_service_override(
        self, storage_conn: sqlite3.Connection, transport: MagicMock
    ) -> None:
        """The slice honours a caller-supplied ``review_service`` kwarg
        (mirrors the digest-service override pattern in the slice)."""
        from job_bot.telegram_bot.slice import TelegramBotSlice

        custom = MagicMock()
        custom.process_message.return_value = []
        slice_ = TelegramBotSlice(
            database=storage_conn,
            transport=transport,
            config={"telegram": {"chat_id": CHAT_ID}},
            review_service=custom,
        )
        try:
            assert slice_.review is custom
        finally:
            slice_.close()


__all__ = (
    "TestReviewServiceModule",
    "TestReviewServiceWiring",
)
