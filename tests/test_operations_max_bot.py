"""Tests for the CLI ``max-bot`` operation (issue #58).

Covers the CLI surface owned by :class:`Operation`:

* argparse flags ``--once``, ``--send-message``, ``--chat-id``, ``--text``;
* ``--once`` runs one polling batch and exits (delegates to the slice
  ``TransportHandler.run(stop_after=1)``);
* ``--send-message`` requires ``--chat-id`` and ``--text`` (exit 1
  otherwise) and forwards to ``adapter.send_message``;
* the ``Operation`` accepts a ``bot_adapter`` (DI-injection from tests /
  the container) — when ``None`` it builds one from ``tool.config``;
* missing ``max.bot_token`` → exit code 1 (no ``SystemExit``);
* the operation never reaches the network: the ``MaxBotSlice`` is wired
  with a stub transport that satisfies :class:`MaxTransportPort`.

Shared helpers (``_make_args``, ``_SimpleTool``, ``_NoopSession``,
``_StubTransport``) live in :mod:`tests.conftest`.
"""

from __future__ import annotations

from typing import Any

import pytest

from hh_applicant_tool.operations.max_bot import Operation
from job_bot.max_bot.handlers.transport_handler import TransportHandler
from job_bot.max_bot.slice import MaxBotSlice

# Pull shared helpers from the project conftest (single source of
# truth for CLI-operation test fixtures, issue #58).
from .conftest import (
    _NoopSession,
    _SimpleTool,
    _StubTransport,
    _make_args,
)


# ─── Helpers ─────────────────────────────────────────────────────


def _make_tool(
    *,
    bot_token: str = "test-max-token",
    api_url: str = "https://botapi.max.ru",
) -> Any:
    """Build a minimal mock ``HHApplicantTool`` (config + session only)."""
    tool = _SimpleTool()
    tool.config = {
        "max": {
            "bot_token": bot_token,
            "api_url": api_url,
        },
    }
    return tool


def _make_slice(transport: _StubTransport | None = None) -> MaxBotSlice:
    from job_bot.max_bot.slice import create_max_bot_slice

    if transport is None:
        transport = _StubTransport()
    return create_max_bot_slice(transport=transport)


# ─── CLI: argument parsing ───────────────────────────────────────


class _ParserHost:
    """Minimal host for :meth:`Operation.setup_parser`."""

    def __init__(self) -> None:
        import argparse

        self.parser = argparse.ArgumentParser()
        Operation().setup_parser(self.parser)


def test_cli_flag_once_is_store_true() -> None:
    host = _ParserHost()
    assert host.parser.parse_args([]).once is False
    assert host.parser.parse_args(["--once"]).once is True


def test_cli_flag_send_message_is_store_true() -> None:
    host = _ParserHost()
    assert host.parser.parse_args([]).send_message is False
    assert host.parser.parse_args(["--send-message"]).send_message is True


@pytest.mark.parametrize(
    "chat_id, text",
    [
        (None, "hi"),       # chat_id missing
        (1, None),          # text missing
        (1, ""),            # text empty
    ],
)
def test_cli_flag_send_message_requires_chat_id_and_text(
    chat_id: int | None, text: str | None
) -> None:
    """Missing or empty ``--chat-id``/``--text`` → exit 1 (smoke-mode guard)."""
    transport = _StubTransport()
    op = Operation(bot_adapter=_make_slice(transport))

    tool = _make_tool()
    args = _make_args(send_message=True, chat_id=chat_id, text=text)
    rc = op.run(tool, args)  # type: ignore[arg-type]

    assert rc == 1
    assert transport.sent == []


# ─── DI injection ───────────────────────────────────────────────


def test_operation_accepts_bot_adapter() -> None:
    """Constructor injection: the slice is used as-is when supplied."""
    transport = _StubTransport()
    slice_ = _make_slice(transport)
    op = Operation(bot_adapter=slice_)

    assert op._bot_adapter is slice_  # type: ignore[attr-defined]


def test_operation_uses_injected_slice_for_send_message() -> None:
    """``--send-message`` calls ``adapter.send_message`` on the injected slice."""
    transport = _StubTransport()
    op = Operation(bot_adapter=_make_slice(transport))

    tool = _make_tool()
    args = _make_args(
        send_message=True, chat_id=42, text="hello from MAX"
    )
    rc = op.run(tool, args)  # type: ignore[arg-type]

    assert rc == 0
    assert transport.sent == [(42, "hello from MAX")]


# ─── --once: one polling cycle and exit ──────────────────────────


def test_once_mode_runs_one_polling_cycle() -> None:
    """``--once`` invokes ``handler.run(stop_after=1)`` and exits 0."""
    transport = _StubTransport()
    op = Operation(bot_adapter=_make_slice(transport))

    tool = _make_tool()
    args = _make_args(once=True)
    rc = op.run(tool, args)  # type: ignore[arg-type]

    assert rc == 0
    # The slice's ``TransportHandler`` polls the transport; with
    # ``stop_after=1`` the loop runs exactly one iteration.
    assert transport._polls == 1


def test_handler_in_slice_is_transport_handler() -> None:
    """Sanity: the slice's handler is a ``TransportHandler``."""
    slice_ = _make_slice()
    assert isinstance(slice_.handler, TransportHandler)


# ─── Build path: _build_adapter ─────────────────────────────────


def test_build_adapter_happy_path() -> None:
    """With a valid bot_token, ``_build_adapter`` returns a working slice.

    Covers the no-injection build path: ``Operation()`` (no
    ``bot_adapter``) + a tool with a valid ``max.bot_token`` config +
    a fake ``tool.session`` → the operation must construct a
    ``MaxBotSlice`` and run one polling cycle.
    """
    op = Operation()  # no adapter → _build_adapter is called

    tool = _SimpleTool()
    tool.config = {
        "max": {
            "bot_token": "happy-path-token",
            "api_url": "https://botapi.max.ru",
        },
    }
    tool.session = _NoopSession()

    args = _make_args(once=True)
    rc = op.run(tool, args)  # type: ignore[arg-type]

    assert rc == 0


def test_missing_bot_token_returns_exit_1() -> None:
    """When ``max.bot_token`` is missing, the operation exits 1.

    We don't pre-build a slice here — the operation should fail before
    touching the network. ``run()`` returns 1 (no ``SystemExit``) so
    the parent's exception/final-block handlers still fire.
    """
    tool = _make_tool(bot_token="")

    op = Operation()  # no adapter → builds one in ``run()``
    args = _make_args(once=True)

    rc = op.run(tool, args)  # type: ignore[arg-type]
    assert rc == 1


# ─── Slice surface sanity ───────────────────────────────────────


def test_slice_exposes_send_message() -> None:
    """``MaxBotSlice.send_message`` is the CLI entry point for ``--send-message``."""
    transport = _StubTransport()
    slice_ = _make_slice(transport)

    ok = slice_.send_message(chat_id=1, text="ping")
    assert ok is True
    assert transport.sent == [(1, "ping")]


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
