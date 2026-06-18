"""Tests for RetryPolicyHandler (issue #201).

The handler classifies exceptions raised by the per-vacancy apply step
into a :class:`RetryDecision` (continue / break). The tests use plain
exception instances — no mocking required, since the handler is
stateless.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from job_bot.application_submit.errors import LimitExceeded
from job_bot.application_submit.handlers.retry_policy_handler import (
    RetryAction,
    RetryDecision,
    RetryPolicyHandler,
)
from job_bot.application_submit.handlers.storage_io_handler import (
    StorageIOHandler,
)
from job_bot.shared.ai._errors import AIError
from job_bot.shared.api.errors import ApiError, BadResponse


def _fake_api_error(message: str = "boom") -> ApiError:
    """Build an ``ApiError`` with a fake response.

    ``ApiError``'s constructor requires a ``requests.Response``; the
    handler only reads ``.message``/``__str__`` for logging, so a
    ``MagicMock`` is fine.
    """
    return ApiError(MagicMock(), {"description": message})


def _fake_limit_exceeded() -> LimitExceeded:
    """Build a ``LimitExceeded`` (inherits ``ClientError`` -> ``ApiError``)."""
    return LimitExceeded(MagicMock(), {"errors": [{"value": "limit_exceeded"}]})


# ─── classify ──────────────────────────────────────────────────────────


class TestRetryPolicyHandlerClassify:
    """``classify`` maps an exception to a :class:`RetryDecision`."""

    def test_limit_exceeded_breaks_with_limit_reached(self) -> None:
        handler = RetryPolicyHandler()
        decision = handler.classify(_fake_limit_exceeded())
        assert decision.action == RetryAction.BREAK
        assert decision.limit_reached is True
        assert decision.do_apply is False

    def test_api_error_continues_with_warning_level(self) -> None:
        handler = RetryPolicyHandler()
        decision = handler.classify(_fake_api_error())
        assert decision.action == RetryAction.CONTINUE
        assert decision.limit_reached is False
        assert decision.do_apply is True

    def test_bad_response_continues(self) -> None:
        handler = RetryPolicyHandler()
        decision = handler.classify(BadResponse("malformed"))
        assert decision.action == RetryAction.CONTINUE
        assert decision.limit_reached is False
        assert decision.do_apply is True

    def test_ai_error_continues(self) -> None:
        handler = RetryPolicyHandler()
        decision = handler.classify(AIError("model down"))
        assert decision.action == RetryAction.CONTINUE
        assert decision.limit_reached is False
        assert decision.do_apply is True

    def test_unknown_exception_continues(self) -> None:
        handler = RetryPolicyHandler()
        decision = handler.classify(RuntimeError("unknown"))
        assert decision.action == RetryAction.CONTINUE
        assert decision.limit_reached is False
        assert decision.do_apply is True

    def test_keyboard_interrupt_continues(self) -> None:
        """``KeyboardInterrupt`` is *not* a ``BaseException`` subclass we
        suppress, but :meth:`run` catches ``Exception`` so it won't
        escape. Classify treats it like any other unknown exception
        (the loop continues)."""
        handler = RetryPolicyHandler()
        decision = handler.classify(KeyboardInterrupt())
        assert decision.action == RetryAction.CONTINUE


# ─── run (success) ─────────────────────────────────────────────────────


class TestRetryPolicyHandlerRunSuccess:
    """``run`` returns a CONTINUE decision when the action succeeds."""

    def test_run_success_returns_continue(self) -> None:
        handler = RetryPolicyHandler()
        decision = handler.run(lambda: None)
        assert decision == RetryDecision(
            action=RetryAction.CONTINUE,
            limit_reached=False,
            do_apply=True,
        )

    def test_run_success_invokes_action(self) -> None:
        handler = RetryPolicyHandler()
        called = []

        def action() -> None:
            called.append(1)

        handler.run(action)
        assert called == [1]

    def test_run_success_returns_decision_not_value(self) -> None:
        """``run`` returns a RetryDecision, not the action's return value."""
        handler = RetryPolicyHandler()
        decision = handler.run(lambda: "ignored")
        assert isinstance(decision, RetryDecision)


# ─── run (exception classification) ─────────────────────────────────────


class TestRetryPolicyHandlerRunClassify:
    """``run`` classifies the exception raised by ``action``."""

    def test_run_limit_exceeded_breaks(self) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise _fake_limit_exceeded()

        decision = handler.run(action, applied_count=5)
        assert decision.action == RetryAction.BREAK
        assert decision.limit_reached is True
        assert decision.do_apply is False

    def test_run_api_error_continues(self) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise _fake_api_error()

        decision = handler.run(action)
        assert decision.action == RetryAction.CONTINUE

    def test_run_bad_response_continues(self) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise BadResponse("malformed")

        decision = handler.run(action)
        assert decision.action == RetryAction.CONTINUE

    def test_run_ai_error_continues(self) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise AIError("model down")

        decision = handler.run(action)
        assert decision.action == RetryAction.CONTINUE

    def test_run_unknown_continues(self) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise RuntimeError("unknown")

        decision = handler.run(action)
        assert decision.action == RetryAction.CONTINUE


# ─── run (logging) ─────────────────────────────────────────────────────


class TestRetryPolicyHandlerRunLogging:
    """``run`` emits the same log messages as the legacy inline policy."""

    def test_limit_exceeded_logs_warning(self, caplog) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise _fake_limit_exceeded()

        with caplog.at_level(logging.WARNING):
            handler.run(action, applied_count=5)
        assert any(
            "лимит" in m.lower() for m in [r.message for r in caplog.records]
        )

    def test_api_error_logs_warning(self, caplog) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise _fake_api_error()

        with caplog.at_level(logging.WARNING):
            handler.run(action)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings  # at least one warning was emitted

    def test_bad_response_logs_error(self, caplog) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise BadResponse("malformed")

        with caplog.at_level(logging.ERROR):
            handler.run(action)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors  # at least one error was emitted

    def test_ai_error_logs_error(self, caplog) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise AIError("model down")

        with caplog.at_level(logging.ERROR):
            handler.run(action)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors

    def test_unknown_exception_logs_error(self, caplog) -> None:
        handler = RetryPolicyHandler()

        def action() -> None:
            raise RuntimeError("boom")

        with caplog.at_level(logging.ERROR):
            handler.run(action)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors


# ─── Protocol satisfaction ──────────────────────────────────────────────────


def test_retry_policy_handler_satisfies_retry_policy_port() -> None:
    """The handler implements the public surface of ``RetryPolicyPort``."""
    from job_bot.application_submit.ports.retry_policy_port import (
        RetryPolicyPort,
    )

    handler: RetryPolicyPort = RetryPolicyHandler()
    assert callable(handler.classify)
    assert callable(handler.run)


def test_storage_io_handler_satisfies_storage_io_port() -> None:
    """The handler implements the public surface of ``StorageIOPort``."""
    from job_bot.application_submit.ports.storage_io_port import StorageIOPort

    handler: StorageIOPort = StorageIOHandler(
        storage=MagicMock(), api_client=MagicMock(), site_parser=MagicMock()
    )
    assert callable(handler.save_vacancy)
    assert callable(handler.load_employer_profile)


# ─── RetryDecision equality ───────────────────────────────────────────


class TestRetryDecision:
    """The decision dataclass is frozen + equality works."""

    def test_decision_is_frozen(self) -> None:
        decision = RetryDecision(action=RetryAction.CONTINUE)
        with pytest.raises((AttributeError, Exception)):
            decision.action = RetryAction.BREAK  # type: ignore[misc]

    def test_decision_equality(self) -> None:
        a = RetryDecision(
            action=RetryAction.BREAK, limit_reached=True, do_apply=False
        )
        b = RetryDecision(
            action=RetryAction.BREAK, limit_reached=True, do_apply=False
        )
        assert a == b
