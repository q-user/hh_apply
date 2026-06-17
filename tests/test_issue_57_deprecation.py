"""Wiring + deprecation checks for issue #57 (Channel Monitoring switchover).

The VSA :class:`ChannelMonitorSlice` (``job_bot.channel_monitoring``)
is the only implementation of the channel-monitor feature in the
codebase — there is no legacy ``hh_applicant_tool.channel_monitor.*``
module to deprecate. This test file verifies the *positive* wiring
contract:

* the VSA slice is importable and exposes the operation-facing surface;
* the CLI ``channel-monitor`` operation in
  ``hh_applicant_tool.operations.channel_monitor`` delegates to the
  VSA slice (no parallel re-implementation);
* the DI container exposes ``create_channel_monitor_slice()`` that
  returns a :class:`ChannelMonitorSlice`;
* importing the operation does NOT import any legacy channel-monitor
  module (sentinel test guarding against future legacy code).

If a legacy module is ever added (e.g. ``hh_applicant_tool.channel_monitor``),
add a parallel deprecation test here mirroring
``tests/test_issue_55_deprecation.py``.
"""

from __future__ import annotations

import importlib
import sys
import warnings
from typing import Any

import pytest

from job_bot.channel_monitoring.handlers.channel_handler import ChannelHandler
from job_bot.channel_monitoring.ports.channel_port import ChannelPort
from job_bot.channel_monitoring.slice import (
    ChannelMonitorSlice,
    create_channel_monitor_slice,
)

# ─── VSA slice surface ──────────────────────────────────────────


def test_slice_factory_is_callable() -> None:
    assert callable(create_channel_monitor_slice)


def test_slice_exposes_required_surface() -> None:
    """The slice exposes ``channels`` (port) and ``handler`` (handler)."""
    slice_ = create_channel_monitor_slice(conn=_empty_conn())
    assert isinstance(slice_, ChannelMonitorSlice)
    assert slice_.channels is not None
    assert isinstance(slice_.handler, ChannelHandler)


def test_channel_port_is_satisfied_by_handler() -> None:
    """The handler is a structural :class:`ChannelPort` implementation."""
    slice_ = create_channel_monitor_slice(conn=_empty_conn())
    assert isinstance(slice_.handler, ChannelPort)


# ─── Operation -> slice wiring ──────────────────────────────────


def test_operation_delegates_to_injected_slice() -> None:
    """``Operation(slice_=slice_)`` forwards to the injected slice."""
    from hh_applicant_tool.operations.channel_monitor import Operation

    calls: list[tuple[str, tuple[Any, ...]]] = []

    class _StubSlice:
        def __init__(self) -> None:
            self.channels = self  # so op.channels.foo() == self.foo()

        def list_channels(self, enabled_only: bool = False) -> list[Any]:
            calls.append(("list_channels", (enabled_only,)))
            return []

    op = Operation(slice_=_StubSlice())
    tool = _SimpleTool()
    rc = op.run(tool, _make_args(list_=True))  # type: ignore[arg-type]

    assert rc == 0
    assert calls == [("list_channels", (False,))]


# ─── DI container wiring ────────────────────────────────────────


def test_container_exposes_channel_monitor_slice() -> None:
    """``AppContainer.channel_monitoring`` returns the slice."""
    from job_bot.container import AppContainer

    tool = _SimpleTool()
    container = AppContainer(tool)
    slice_ = container.channel_monitoring

    assert isinstance(slice_, ChannelMonitorSlice)
    assert isinstance(slice_.handler, ChannelHandler)


def test_container_channel_monitor_slice_is_memoised() -> None:
    """Repeated accesses of the ``channel_monitoring`` property return
    the same slice instance (``@cached_property``)."""
    from job_bot.container import AppContainer

    tool = _SimpleTool()
    container = AppContainer(tool)
    a = container.channel_monitoring
    b = container.channel_monitoring
    assert a is b


# ─── No legacy module (sentinel) ────────────────────────────────


@pytest.mark.parametrize(
    "legacy_module",
    [
        "hh_applicant_tool.channel_monitor",
        "hh_applicant_tool.channel_monitor.handler",
    ],
)
def test_legacy_channel_monitor_module_is_not_present(
    legacy_module: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No legacy ``hh_applicant_tool.channel_monitor.*`` module exists.

    The channel-monitor feature was introduced as a VSA slice from
    day one (issue #57), so there is nothing to deprecate. This
    sentinel test will start failing if a legacy module is ever
    added, forcing the author to add a real deprecation entry.
    """
    monkeypatch.delitem(sys.modules, legacy_module, raising=False)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(legacy_module)


# ─── Defensive: no DeprecationWarning on operation import ───────


def test_operation_does_not_emit_deprecation_warnings() -> None:
    """Importing the operation must not emit any ``DeprecationWarning``."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("hh_applicant_tool.operations.channel_monitor")
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations == [], (
        "Operation must not emit DeprecationWarning on import; got: "
        f"{[str(w.message) for w in deprecations]}"
    )


# ─── Helpers ─────────────────────────────────────────────────────


class _SimpleTool:
    """Bare-bones stand-in for ``HHApplicantTool`` (just for DI tests)."""

    def __init__(self) -> None:
        self.db = _empty_conn()


def _empty_conn() -> Any:
    import sqlite3

    return sqlite3.connect(":memory:")


def _make_args(
    *,
    list_: bool = False,
    enabled: bool = False,
    add: bool = False,
    name: str | None = None,
    channel_id: str | None = None,
    keywords: str | None = None,
    remove: bool = False,
    parse: bool = False,
    text: str | None = None,
) -> Any:
    import argparse

    return argparse.Namespace(
        list=list_,
        enabled=enabled,
        add=add,
        name=name,
        channel_id=channel_id,
        keywords=keywords,
        remove=remove,
        parse=parse,
        text=text,
        profile_id="default",
        config_dir=None,
        verbosity=0,
        api_delay=None,
        user_agent=None,
        proxy_url=None,
        openai_proxy_url=None,
        operation_run=None,
    )


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
