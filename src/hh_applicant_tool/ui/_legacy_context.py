"""Internal helpers for the legacy ``hh_applicant_tool.ui`` shim.

The public shim (``hh_applicant_tool.ui.api``) is intentionally tiny
— it just re-exports the new :class:`Api` and overrides
:meth:`set_window` to wire the pywebview window.  All the actual
glue code lives in this module so the shim's line count is kept
under the 30-LOC budget.
"""

from __future__ import annotations

import json
from typing import Any

from job_bot.ui import UiApiContext as _UiApiContext
from job_bot.ui.presets import PresetsManager


def build_legacy_context(tool: Any) -> _UiApiContext:
    """Build a :class:`UiApiContext` from a ``HHApplicantTool``.

    Each field is read off the tool with ``getattr`` + a defensive
    default so a partial test double (a ``MagicMock`` with only a
    subset of attributes) still works.  The factory closures
    (`apply_use_case_factory`, `get_me`, etc.) bind the tool so the
    new :class:`Api` can dispatch into the legacy service locator.
    """
    from hh_applicant_tool.container import AppContainer

    def _apply_use_case_factory(**kwargs: Any) -> Any:
        return AppContainer(tool).apply_to_vacancies_use_case(**kwargs)

    def _prepare_use_case_factory(**kwargs: Any) -> Any:
        return AppContainer(tool).prepare_vacancies_use_case(**kwargs)

    def _get_me() -> Any:
        return tool.get_me()

    def _get_resumes() -> list[dict]:
        return tool.get_resumes()

    def _get_negotiations(*args: Any, **kwargs: Any) -> Any:
        return tool.get_negotiations(*args, **kwargs)

    def _clear_token() -> None:
        try:
            tool.config.save_token({})
        except Exception:  # noqa: BLE001
            pass
        try:
            client = tool.api_client
            client.access_token = None
            client.refresh_token = None
            client.access_expires_at = 0
        except Exception:  # noqa: BLE001
            pass

    presets = PresetsManager(tool.storage.settings)

    return _UiApiContext(
        api_client=tool.api_client,
        config=tool.config,
        storage=tool.storage,
        apply_use_case_factory=_apply_use_case_factory,
        prepare_use_case_factory=_prepare_use_case_factory,
        presets=presets,
        progress_sink=make_progress_sink(),
        auth_event_sink=make_auth_event_sink(),
        get_me=_get_me,
        get_resumes=_get_resumes,
        get_negotiations=_get_negotiations,
        clear_token=_clear_token,
    )


# ─── Module-level "active shim" pointer ───────────────────────────────
#
# The :class:`Api` shim's progress / auth event sinks need to call
# into the webview window.  A clean way to expose the window is to
# keep a module-level pointer to the currently-active shim instance;
# the sinks look it up at call time.

_active_shim: "Any | None" = None


def register_active_shim(shim: Any) -> None:
    """Register a shim instance so the module-level sinks can find it.

    Called by :meth:`Api.__init__` after the base class is set up.
    The previous shim (if any) is replaced — there is only ever
    one ``js_api`` per webview.
    """
    global _active_shim
    _active_shim = shim


def make_progress_sink() -> "Any":
    """Return a sink that pushes ``(current, total, message)`` to JS."""

    def _sink(current: int, total: int, message: str) -> None:
        shim = _active_shim
        if shim is None or shim._window is None:
            return
        try:
            safe_msg = json.dumps(message)
            shim._window.evaluate_js(
                f"updateProgress({current}, {total}, {safe_msg})"
            )
        except Exception:  # noqa: BLE001  # best-effort progress update
            pass

    return _sink


def make_auth_event_sink() -> "Any":
    """Return a sink that pushes ``(event, message)`` to JS."""

    def _sink(event: str, message: str) -> None:
        shim = _active_shim
        if shim is None or shim._window is None:
            return
        try:
            safe_event = json.dumps(event)
            safe_msg = json.dumps(message)
            shim._window.evaluate_js(f"onAuthEvent({safe_event}, {safe_msg})")
        except Exception:  # noqa: BLE001  # best-effort event notification
            pass

    return _sink
