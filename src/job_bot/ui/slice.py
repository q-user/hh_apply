"""The :class:`UiSlice` factory for the ``job_bot.ui`` slice (VSA — Issue #150).

Wires a :class:`UiApiContext` from the slice's dependencies and
exposes the resulting :class:`Api` and :class:`PresetsManager` as
cached properties.  The slice is intentionally a thin factory — the
:class:`Api` and :class:`PresetsManager` are the slice's reusable
surface; the slice itself just memoises them.

Usage::

    slice_ = UiSlice(
        api_client=api_client,
        config=config,
        storage=storage,
        apply_use_case_factory=...,
        prepare_use_case_factory=...,
        presets=PresetsManager(storage.settings),
        progress_sink=lambda c, t, m: ...,
        auth_event_sink=lambda e, m: ...,
    )
    webview.start(...)
    slice_.set_window(window)  # propagates to the underlying Api
"""

from __future__ import annotations

from functools import cached_property
from typing import Any, Callable

from .api import Api
from .ports import (
    ConfigPort,
    HhApiClientPort,
    LegacyUseCasePort,
    StoragePort,
    UiApiContext,
)


# Module-level defaults for the optional :class:`UiApiContext` helpers.
# Defined at module scope (not as lambda defaults) so the VSA slice can
# introspect them in tests without poking at the dataclass.
def _default_get_me() -> Any:
    return {"first_name": "", "last_name": "", "email": ""}


def _default_get_resumes() -> list[dict]:
    return []


def _default_get_negotiations(*args: Any, **kwargs: Any) -> Any:
    return iter(())


def _default_clear_token() -> None:
    return None


class UiSlice:
    """Composition root for the ``job_bot.ui`` slice.

    Issue #150: the slice bundles the :class:`Api` (the pywebview
    ``js_api`` bridge) and the :class:`PresetsManager`.  Both are
    memoised behind cached properties; the slice is a single
    argument-passing object so the webview's ``create_window``
    doesn't have to know about the :class:`UiApiContext` shape.

    The :class:`Api` is constructed from a :class:`UiApiContext`
    that the slice builds from the dependencies it receives.  The
    ``progress_sink`` and ``auth_event_sink`` callbacks come from
    the caller — typically a small adapter that calls
    ``window.evaluate_js(...)`` on a pywebview window.
    """

    def __init__(
        self,
        *,
        api_client: HhApiClientPort,
        config: ConfigPort,
        storage: StoragePort,
        apply_use_case_factory: Callable[..., LegacyUseCasePort],
        prepare_use_case_factory: Callable[..., LegacyUseCasePort],
        presets: Any,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
        window: Any | None = None,
        # Optional helpers — bound by the legacy shim, replaced by
        # port adapters in the VSA slice.
        get_me: Callable[[], Any] | None = None,
        get_resumes: Callable[[], list[dict]] | None = None,
        get_negotiations: Callable[..., Any] | None = None,
        clear_token: Callable[[], None] | None = None,
    ) -> None:
        # The context is built once; ``set_window`` mutates the
        # ``window`` field later (the dataclass is not frozen).
        self._ctx = UiApiContext(
            api_client=api_client,
            config=config,
            storage=storage,
            apply_use_case_factory=apply_use_case_factory,
            prepare_use_case_factory=prepare_use_case_factory,
            presets=presets,
            progress_sink=progress_sink,
            auth_event_sink=auth_event_sink,
            window=window,
            get_me=get_me or _default_get_me,
            get_resumes=get_resumes or _default_get_resumes,
            get_negotiations=get_negotiations or _default_get_negotiations,
            clear_token=clear_token or _default_clear_token,
        )

    @cached_property
    def context(self) -> UiApiContext:
        """The :class:`UiApiContext` this slice wraps."""
        return self._ctx

    @cached_property
    def api(self) -> Api:
        """The :class:`Api` instance, ready to be registered as ``js_api``."""
        return Api(self._ctx)

    @cached_property
    def presets(self) -> Any:
        """The :class:`PresetsManager` (passed through for direct access)."""
        return self._ctx.presets

    def set_window(self, window: Any) -> None:
        """Propagate the pywebview window into the :class:`Api`'s context.

        Issue #150 acceptance criterion: this method is what
        ``create_window`` calls once the webview is up.  The
        :class:`Api`'s ``progress_sink`` / ``auth_event_sink``
        callbacks can then call into ``window.evaluate_js(...)``
        to notify the JS side.
        """
        self._ctx.window = window


__all__ = ["UiSlice"]
