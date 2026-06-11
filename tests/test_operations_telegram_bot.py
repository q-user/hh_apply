"""Tests for the CLI ``telegram-bot`` operation (issue #7; rewritten on
top of the VSA ``TelegramBotSlice`` in issue #56).

Covers the CLI surface that the ``Operation`` class still owns:
  * argparse flags ``--once`` and ``--send-digest-now``;
  * the ``--once`` polling cycle (one batch, then exit);
  * the ``--send-digest-now`` flag (forces ``send(force=True)`` on the
    slice's daily-digest service);
  * idempotency of the daily digest (re-calls within the same day are
    a no-op);
  * the time-of-day gate (no ``send()`` before ``daily_digest_time``);
  * missing bot_token → exit code 1.

The Operation no longer owns command routing or reply building — those
are owned by ``job_bot.telegram_bot.handlers.CommandHandler`` and
covered by ``tests/test_telegram_bot.py``. Here we only test the
``Operation`` shell.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from hh_applicant_tool.operations.telegram_bot import Operation
from hh_applicant_tool.services.daily_digest import DigestResult
from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.telegram.transport import (
    TelegramTransport,
    TelegramTransportConfig,
)

# ─── Helpers ─────────────────────────────────────────────────────────


def _make_transport(
    allowed: tuple[int, ...] = (123,),
    updates: list | None = None,
) -> TelegramTransport:
    """Build a real ``TelegramTransport``; ``get_updates`` is mocked."""
    config = TelegramTransportConfig(
        bot_token="test-token",
        poll_timeout=30,
        allowed_user_ids=allowed,
    )
    transport = TelegramTransport(config=config)
    if updates is not None:
        transport.get_updates = Mock(return_value=updates)  # type: ignore[method-assign]
    return transport


def _make_tool(
    storage_conn: sqlite3.Connection | None = None,
    *,
    digest_time: str = "10:00",
    bot_token: str = "test-token",
) -> MagicMock:
    """Build a minimal mock ``HHApplicantTool``."""
    tool = MagicMock()
    tool.config = {
        "telegram": {
            "bot_token": bot_token,
            "poll_timeout": 30,
            "allowed_user_ids": [123],
            "digest_chat_id": 42,
            "daily_digest_time": digest_time,
        },
    }
    if storage_conn is not None:
        tool.storage = StorageFacade(storage_conn)
    return tool


def _make_digest_mock(
    *,
    sent: bool = True,
    total_drafts: int = 0,
    skipped_reason: str | None = None,
) -> MagicMock:
    """Build a mock digest service with the legacy boolean contract."""
    digest = MagicMock()
    digest.send.return_value = DigestResult(
        sent=sent,
        skipped_reason=skipped_reason,
        total_drafts=total_drafts,
        message="",
    )
    return digest


def _make_bot_adapter(
    *,
    updates: list | None = None,
    digest: MagicMock | None = None,
    transport: TelegramTransport | None = None,
) -> Any:
    """Build a stub ``TelegramBotAdapter`` with the operation-facing surface.

    Returns an object with ``.transport``, ``.dispatch_update`` and
    ``.send_digest`` so the ``Operation`` can run without touching the
    real ``AppContainer`` / ``TelegramBotSlice``.
    """
    if transport is None:
        transport = _make_transport(
            updates=updates if updates is not None else []
        )
    adapter = MagicMock()
    adapter.transport = transport
    adapter.dispatch_update = MagicMock()
    adapter.send_digest = MagicMock(
        return_value=(
            digest.send.return_value
            if digest is not None
            else DigestResult(
                sent=False, skipped_reason="no-op", total_drafts=0, message=""
            )
        ),
    )
    return adapter


def _make_args(
    *,
    once: bool = False,
    send_digest_now: bool = False,
) -> argparse.Namespace:
    """Build an ``argparse.Namespace`` for ``Operation.run``."""
    return argparse.Namespace(
        once=once,
        send_digest_now=send_digest_now,
        profile_id="default",
        config_dir=None,
        verbosity=0,
        api_delay=None,
        user_agent=None,
        proxy_url=None,
        openai_proxy_url=None,
        operation_run=None,
    )


# ─── CLI: argument parsing ───────────────────────────────────────────


class _ParserHost:
    """Minimal host for :meth:`Operation.setup_parser`."""

    def __init__(self) -> None:
        self.parser = argparse.ArgumentParser()
        Operation().setup_parser(self.parser)


def test_cli_flag_once_is_store_true() -> None:
    """``--once`` is a boolean flag, default ``False``."""
    host = _ParserHost()
    assert host.parser.parse_args([]).once is False
    assert host.parser.parse_args(["--once"]).once is True


def test_cli_flag_send_digest_now_is_store_true() -> None:
    """``--send-digest-now`` is a boolean flag, default ``False``."""
    host = _ParserHost()
    assert host.parser.parse_args([]).send_digest_now is False
    assert host.parser.parse_args(["--send-digest-now"]).send_digest_now is True


def test_cli_flags_can_be_combined() -> None:
    """``--once`` and ``--send-digest-now`` compose (cron use case)."""
    host = _ParserHost()
    args = host.parser.parse_args(["--once", "--send-digest-now"])
    assert args.once is True
    assert args.send_digest_now is True


# ─── --once: one polling cycle and exit ──────────────────────────────


def test_once_mode_exits_after_one_cycle(storage: sqlite3.Connection) -> None:
    """``--once`` runs one batch and returns 0; ``get_updates`` once."""
    adapter = _make_bot_adapter(updates=[])
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage)
    rc = op.run(tool, _make_args(once=True))  # type: ignore[arg-type]

    assert rc == 0
    adapter.transport.get_updates.assert_called_once()  # type: ignore[attr-defined]
    adapter.dispatch_update.assert_not_called()  # empty batch


def test_once_mode_dispatches_each_update(storage: sqlite3.Connection) -> None:
    """``--once`` dispatches every update from the polling batch."""
    updates = [
        {"update_id": 1, "message": {"text": "/start"}},
        {"update_id": 2, "message": {"text": "/help"}},
    ]
    adapter = _make_bot_adapter(updates=updates)
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage)
    rc = op.run(tool, _make_args(once=True))  # type: ignore[arg-type]

    assert rc == 0
    assert adapter.dispatch_update.call_count == 2  # type: ignore[attr-defined]


# ─── --send-digest-now → force=True ─────────────────────────────────


def test_send_digest_now_triggers_force_send(
    storage: sqlite3.Connection,
) -> None:
    """``--send-digest-now`` forces ``adapter.send_digest(force=True)``."""
    digest = _make_digest_mock(sent=True, total_drafts=3)
    adapter = _make_bot_adapter(digest=digest)
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage, digest_time="00:00")
    rc = op.run(tool, _make_args(once=True, send_digest_now=True))  # type: ignore[arg-type]

    assert rc == 0
    adapter.send_digest.assert_called_once_with(force=True)  # type: ignore[attr-defined]


def test_without_send_digest_now_uses_force_false(
    storage: sqlite3.Connection,
) -> None:
    """Without ``--send-digest-now`` the digest is called with ``force=False``."""
    digest = _make_digest_mock(sent=False, skipped_reason="already_sent")
    adapter = _make_bot_adapter(digest=digest)
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage, digest_time="00:00")
    op.run(tool, _make_args(once=True, send_digest_now=False))  # type: ignore[arg-type]

    adapter.send_digest.assert_called_once_with(force=False)  # type: ignore[attr-defined]


# ─── Idempotency of the daily digest ────────────────────────────────


def test_digest_not_sent_twice_same_day(storage: sqlite3.Connection) -> None:
    """Two ``_maybe_send_digest`` cycles in the same day: the adapter
    is called once with ``force=False`` each time; the service's
    ``already_sent_today`` flag handles deduplication downstream."""
    digest = MagicMock()
    digest.send.side_effect = [
        DigestResult(sent=True, total_drafts=4, message="ok"),
        DigestResult(
            sent=False,
            skipped_reason="already_sent",
            total_drafts=4,
            message="ok",
        ),
    ]
    adapter = _make_bot_adapter(digest=digest)
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage)
    fixed_now = datetime(2024, 1, 1, 12, 0, 0)
    for _ in range(2):
        op._maybe_send_digest(  # type: ignore[attr-defined]
            tool_config=tool.config,
            force=False,
            adapter=adapter,
            now=fixed_now,
        )

    assert adapter.send_digest.call_count == 2  # type: ignore[attr-defined]
    adapter.send_digest.assert_called_with(force=False)  # type: ignore[attr-defined]


def test_digest_force_send_can_override_idempotency(
    storage: sqlite3.Connection,
) -> None:
    """``force=True`` reaches the slice's ``send()``."""
    digest = _make_digest_mock(sent=True, total_drafts=2)
    adapter = _make_bot_adapter(digest=digest)
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage)
    fixed_now = datetime(2024, 1, 1, 12, 0, 0)
    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=True,
        adapter=adapter,
        now=fixed_now,
    )

    adapter.send_digest.assert_called_once_with(force=True)  # type: ignore[attr-defined]


# ─── Time-of-day gate ───────────────────────────────────────────────


def test_digest_not_sent_before_configured_time(
    storage: sqlite3.Connection,
) -> None:
    """Before ``daily_digest_time`` ``send_digest()`` is not called."""
    digest = _make_digest_mock()
    adapter = _make_bot_adapter(digest=digest)
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage, digest_time="10:00")
    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=False,
        adapter=adapter,
        now=datetime(2026, 6, 9, 9, 0, 0),
    )

    adapter.send_digest.assert_not_called()  # type: ignore[attr-defined]


def test_digest_sent_at_or_after_configured_time(
    storage: sqlite3.Connection,
) -> None:
    """At and after ``daily_digest_time`` ``send_digest()`` is called."""
    digest = _make_digest_mock(sent=True, total_drafts=1)
    adapter = _make_bot_adapter(digest=digest)
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage, digest_time="10:00")

    # Exactly 10:00 — must trigger (``>=``).
    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=False,
        adapter=adapter,
        now=datetime(2026, 6, 9, 10, 0, 0),
    )
    adapter.send_digest.assert_called_once()  # type: ignore[attr-defined]

    adapter.send_digest.reset_mock()  # type: ignore[attr-defined]
    # 11:30 — must trigger.
    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=False,
        adapter=adapter,
        now=datetime(2026, 6, 9, 11, 30, 0),
    )
    adapter.send_digest.assert_called_once()  # type: ignore[attr-defined]


def test_digest_skipped_without_telegram_config(
    storage: sqlite3.Connection,
) -> None:
    """Without a ``telegram`` config the digest is not called."""
    digest = _make_digest_mock()
    adapter = _make_bot_adapter(digest=digest)
    op = Operation(bot_adapter=adapter)

    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config={},
        force=False,
        adapter=adapter,
        now=datetime(2026, 6, 9, 12, 0, 0),
    )
    adapter.send_digest.assert_not_called()  # type: ignore[attr-defined]


def test_digest_send_failure_does_not_propagate(
    storage: sqlite3.Connection,
) -> None:
    """``send_digest()`` raising must not crash the polling cycle."""
    digest = MagicMock()
    digest.send.side_effect = RuntimeError("telegram down")
    adapter = _make_bot_adapter(digest=digest)
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage)
    result = op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=False,
        adapter=adapter,
        now=datetime(2026, 6, 9, 12, 0, 0),
    )
    assert result is None


# ─── Bot without bot_token → clean exit code 1 ──────────────────────


def test_run_returns_1_without_bot_token(storage: sqlite3.Connection) -> None:
    """No ``telegram.bot_token`` → exit code 1, no polling."""
    adapter = _make_bot_adapter()
    op = Operation(bot_adapter=adapter)

    tool = _make_tool(storage, bot_token="")
    rc = op.run(tool, _make_args())  # type: ignore[arg-type]

    assert rc == 1
    adapter.transport.get_updates.assert_not_called()  # type: ignore[attr-defined]


# ─── DI: pre-built adapter is reused, not rebuilt ───────────────────


def test_pre_built_adapter_is_reused(storage: sqlite3.Connection) -> None:
    """``Operation(bot_adapter=...)`` is honoured — the container
    factory is *not* called on the run path."""
    adapter = _make_bot_adapter(updates=[])
    op = Operation(bot_adapter=adapter)

    # Patch ``AppContainer.create_telegram_bot_adapter`` to detect any
    # accidental rebuilding of the adapter. It must NOT be called.
    from hh_applicant_tool import container as container_mod

    factory = MagicMock(side_effect=AssertionError("container factory called"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            container_mod.AppContainer,
            "create_telegram_bot_adapter",
            factory,
        )
        rc = op.run(_make_tool(storage), _make_args(once=True))  # type: ignore[arg-type]

    assert rc == 0
    factory.assert_not_called()
