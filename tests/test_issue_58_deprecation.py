"""Wiring + deprecation checks for issue #58 (MAX Bot slice switchover).

The VSA ``MaxBotSlice`` (``job_bot.max_bot``) is the only implementation
of the MAX messenger bot in the codebase — there is no legacy
``hh_applicant_tool.max_bot.*`` module to deprecate. This test file
therefore verifies the *positive* wiring contract:

* the VSA slice is importable and the public surface matches the
  acceptance criteria of issue #58;
* the CLI ``max-bot`` operation in ``hh_applicant_tool.operations.max_bot``
  delegates to the VSA slice (no parallel re-implementation);
* the DI container exposes ``create_max_bot_adapter()`` that returns a
  slice whose transport satisfies :class:`MaxTransportPort`;
* importing the operation does NOT import any legacy MAX transport
  module (i.e. nothing in the operation's import graph survives that
  would mask the slice).

If a legacy module is ever added (e.g. ``hh_applicant_tool.max_bot``),
add a parallel deprecation test here mirroring ``test_issue_55_deprecation.py``.

Shared helpers (``_make_args``, ``_SimpleTool``, ``_NoopSession``) live
in :mod:`tests.conftest`.
"""

from __future__ import annotations

import importlib
import sys
import warnings
from typing import Any

import pytest

from job_bot.max_bot import MaxBotSlice, create_max_bot_slice
from job_bot.max_bot.handlers.transport_handler import TransportHandler
from job_bot.max_bot.ports.transport_port import MaxTransportPort

from .conftest import (
    _make_args,
    _NoopSession,
    _SimpleTool,
)

# ─── VSA slice surface ──────────────────────────────────────────


def test_max_bot_slice_factory_is_callable() -> None:
    """``create_max_bot_slice`` is the documented public entry point."""
    assert callable(create_max_bot_slice)


def test_max_bot_slice_exposes_required_surface() -> None:
    """The slice's public surface covers what the CLI operation needs.

    Issue #58 requires that ``Operation.run`` works against the slice
    with no shim. We assert the contract the operation depends on:
    ``.transport``, ``.handler`` and ``.send_message``.
    """

    class _Stub:
        def send_message(self, chat_id: int, text: str) -> bool:
            return True

        def get_updates(
            self, offset: int | None = None, timeout: int = 30
        ) -> list[dict[str, Any]]:
            return []

    slice_ = create_max_bot_slice(transport=_Stub())  # type: ignore[arg-type]
    assert isinstance(slice_, MaxBotSlice)
    assert slice_.handler is not None
    assert isinstance(slice_.handler, TransportHandler)
    assert slice_.send_message(chat_id=1, text="x") is True


def test_transport_port_is_satisfied_by_structural_stub() -> None:
    """Any object with the documented methods is a valid ``MaxTransportPort``."""

    class _Stub:
        def send_message(self, chat_id: int, text: str) -> bool:
            return True

        def get_updates(
            self, offset: int | None = None, timeout: int = 30
        ) -> list[dict[str, Any]]:
            return []

        # MaxTransportPort declares allowed_user_ids as part of the
        # access-control surface (mirrored onto the transport by the
        # MaxBotSlice). A no-op tuple keeps the stub structurally
        # conformant without enabling any user filtering.
        allowed_user_ids: tuple[int, ...] = ()

    assert isinstance(_Stub(), MaxTransportPort)


# ─── Operation -> slice wiring ──────────────────────────────────


def test_max_bot_operation_delegates_to_slice() -> None:
    """``Operation(bot_adapter=slice_)`` uses the slice for polling/sending."""
    from hh_applicant_tool.operations.max_bot import Operation

    sent: list[tuple[int, str]] = []
    polls: list[int | None] = []

    class _Recording:
        def send_message(self, chat_id: int, text: str) -> bool:
            sent.append((chat_id, text))
            return True

        def get_updates(
            self, offset: int | None = None, timeout: int = 30
        ) -> list[dict[str, Any]]:
            polls.append(offset)
            return []

    slice_ = create_max_bot_slice(transport=_Recording())  # type: ignore[arg-type]
    op = Operation(bot_adapter=slice_)

    tool = _SimpleTool()
    args = _make_args(once=True)
    rc = op.run(tool, args)  # type: ignore[arg-type]

    assert rc == 0
    assert len(polls) >= 1  # --once triggered at least one poll cycle
    assert sent == []  # --once never sends; only --send-message does


def test_max_bot_operation_send_message_uses_slice() -> None:
    """``--send-message`` calls ``slice.send_message`` exactly once."""
    from hh_applicant_tool.operations.max_bot import Operation

    sent: list[tuple[int, str]] = []

    class _Recording:
        def send_message(self, chat_id: int, text: str) -> bool:
            sent.append((chat_id, text))
            return True

        def get_updates(
            self, offset: int | None = None, timeout: int = 30
        ) -> list[dict[str, Any]]:
            return []

    slice_ = create_max_bot_slice(transport=_Recording())  # type: ignore[arg-type]
    op = Operation(bot_adapter=slice_)

    tool = _SimpleTool()
    args = _make_args(send_message=True, chat_id=99, text="hi")
    rc = op.run(tool, args)  # type: ignore[arg-type]

    assert rc == 0
    assert sent == [(99, "hi")]


# ─── DI container wiring ────────────────────────────────────────


def test_container_exposes_max_bot_slice() -> None:
    """``AppContainer.max_bot`` returns a slice whose transport satisfies
    :class:`MaxTransportPort`."""
    from job_bot.container import AppContainer

    tool = _SimpleTool()
    tool.config = {
        "max": {
            "bot_token": "di-test-token",
            "api_url": "https://botapi.max.ru",
        },
    }
    # Provide a stub session so the transport can be constructed without
    # touching the network.
    tool.session = _NoopSession()

    container = AppContainer(tool)
    slice_ = container.max_bot

    # The slice is the operation's surface (``.transport``, ``.handler``,
    # ``.send_message``). Use ``isinstance`` (not ``hasattr``) so a wrong
    # object can't slip through — the test fails if the container returns
    # anything other than a real :class:`MaxBotSlice` whose ``.handler``
    # is a :class:`TransportHandler`.
    assert isinstance(slice_, MaxBotSlice)
    assert isinstance(slice_.handler, TransportHandler)
    assert slice_.transport is not None


def test_container_max_bot_slice_is_memoised() -> None:
    """``max_bot`` returns the same slice across accesses (``@cached_property``)."""
    from job_bot.container import AppContainer

    tool = _SimpleTool()
    tool.config = {
        "max": {
            "bot_token": "di-test-token",
            "api_url": "https://botapi.max.ru",
        },
    }
    tool.session = _NoopSession()

    container = AppContainer(tool)
    a = container.max_bot
    b = container.max_bot
    assert a is b


# ─── No legacy MAX transport module ─────────────────────────────


@pytest.mark.parametrize(
    "legacy_module",
    [
        "hh_applicant_tool.max_bot",
        "hh_applicant_tool.max_bot.transport",
    ],
)
def test_legacy_max_bot_module_is_not_present(
    legacy_module: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No legacy ``hh_applicant_tool.max_bot.*`` module exists.

    The MAX integration was introduced as a VSA slice from day one
    (issue #58), so there is nothing to deprecate. This test acts as
    a sentinel: if a legacy module is ever added, this test will start
    *failing* and force the author to add a real deprecation entry
    (mirroring ``test_issue_55_deprecation.py``).
    """
    # Ensure no module is cached from earlier tests.
    monkeypatch.delitem(sys.modules, legacy_module, raising=False)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(legacy_module)


# ─── Defensive: no DeprecationWarning on operation import ───────


def test_operation_does_not_emit_deprecation_warnings() -> None:
    """Importing the operation must not emit any ``DeprecationWarning``.

    Defensive: catches accidental imports of a legacy MAX transport
    module (the test above catches it structurally; this catches it
    via Python's warning machinery).
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("hh_applicant_tool.operations.max_bot")
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations == [], (
        "Operation must not emit DeprecationWarning on import; got: "
        f"{[str(w.message) for w in deprecations]}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
