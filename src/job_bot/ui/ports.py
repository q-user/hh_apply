"""Ports for the ``job_bot.ui`` slice (VSA — Issue #150).

Defines the cross-slice contract the :class:`job_bot.ui.api.Api`
depends on.  The :class:`UiApiContext` dataclass bundles the eight
dependencies the :class:`Api` historically read out of
``HHApplicantTool`` — see issue #150 for the full rationale.

Why a dataclass, not a Protocol
-------------------------------

A :class:`UiApiContext` is a **bundle of dependencies**, not a single
abstract interface.  Each dependency is itself a Protocol (or a
concrete class) with its own surface.  The dataclass just collects
them and gives the slice's factory a single argument to pass around —
this keeps the legacy ``Api.__init__`` shim (which has to build the
context from a ``HHApplicantTool``-shaped object) cheap and explicit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HhApiClientPort(Protocol):
    """Subset of the HH.ru API client surface the :class:`Api` uses.

    Mirrors the legacy ``HHApiClient`` methods the :class:`Api`
    historically called directly on ``self._tool.api_client``:

    * :attr:`access_token` / :attr:`refresh_token` /
      :attr:`access_expires_at` — read/written by ``get_status``,
      ``logout`` and the auth worker.
    * :attr:`delay` — settable (used by ``apply_vacancies`` to honour
      the ``api_delay`` payload).
    * :meth:`get` — called by ``get_areas`` / ``get_professional_roles``
      / ``get_industries``.

    Other methods (``post`` / ``put`` / ``delete``) are declared so the
    Protocol is broad enough to accept the VSA ``HHApiClient`` from
    :mod:`job_bot.shared.api.client`, but the UI doesn't use them.
    """

    access_token: str | None
    refresh_token: str | None
    access_expires_at: int
    delay: float

    def get(self, endpoint: str, params: dict | None = None) -> Any:
        """Perform a ``GET`` request and return the parsed JSON body."""
        ...

    def post(self, endpoint: str, payload: dict | None = None) -> Any: ...

    def put(self, endpoint: str, json_data: dict | None = None) -> Any: ...

    def delete(self, endpoint: str) -> Any: ...


@runtime_checkable
class ConfigPort(Protocol):
    """Dict-like config surface the :class:`Api` consumes.

    Mirrors ``_ConfigAdapter`` (the legacy config shim) and the
    ``MockConfig`` test double in ``tests/test_ui_api.py``:

    * :meth:`get` — for ``_clear_token`` and ``save_config``
      ("config.get("token", {}).get(...)" etc.).
    * ``dict(self)`` — for ``get_config`` (the slice iterates the
      config to mask secrets).
    * :meth:`save` — for ``save_config`` (merges updates then persists).
    * :meth:`save_token` — for ``_clear_token`` (writes an empty token
      dict to wipe the credentials).

    The Protocol is structural so the VSA slice can be tested with the
    ``_FakeConfig`` test double in ``tests/vsa/test_ui_slice.py``.
    """

    def get(self, key: str, default: Any = None) -> Any: ...

    def __getitem__(self, key: str) -> Any: ...

    def __contains__(self, key: str) -> bool: ...

    def __iter__(self): ...  # type: ignore[no-untyped-def]

    def __len__(self) -> int: ...

    def save(self, **kwargs: Any) -> None: ...

    def save_token(self, token: dict[str, Any]) -> None: ...


@runtime_checkable
class StoragePort(Protocol):
    """Storage surface the :class:`Api` needs.

    Mirrors the VSA :class:`StoragePort` shape (declared in
    :mod:`job_bot.shared.storage.ports`) but limited to the two repos
    the :class:`Api` actually touches:

    * :attr:`settings` — backs :class:`PresetsManager`.
    * :attr:`negotiations` — used by ``get_negotiations_from_db``,
      ``refresh_negotiations`` and ``get_statistics``.
    * :attr:`database` — exposed for the legacy shim's
      ``__init__`` (it needs the path for the ``:memory:`` SQLite
      workaround).
    """

    database: Any

    @property
    def settings(self) -> Any: ...

    @property
    def negotiations(self) -> Any: ...


@runtime_checkable
class LegacyUseCasePort(Protocol):
    """Minimal contract the :class:`Api` needs from the apply/prepare
    use cases.

    The real
    :class:`hh_applicant_tool.application.use_cases.apply_to_vacancies.ApplyToVacanciesUseCase`
    satisfies this Protocol structurally — no explicit import in the
    UI slice.  The slice is therefore decoupled from the legacy use
    case class (and from the VSA replacement under construction in
    issue #145).
    """

    def execute(self, command: Any, *, cancel_event: Any = None) -> Any:
        """Run the use case; return whatever the use case returns."""
        ...


# ─── UiApiContext ─────────────────────────────────────────────────────


@dataclass
class UiApiContext:
    """The :class:`Api`'s full DI surface.

    Issue #150: each of the 19 public methods on the new
    :class:`job_bot.ui.api.Api` is a 1-3 line dispatch into one of
    these fields.  The dataclass is mutable (so :meth:`UiSlice.set_window`
    can swap the window reference after the webview is created) but
    every field is a port (Protocol or callable) — there is no hidden
    state.

    Attributes:
        api_client: HH.ru HTTP client (with auth-attribute access).
        config: Dict-like config (with ``save`` / ``save_token``).
        storage: The 15-repo facade (we use ``settings`` and
            ``negotiations``; the rest are exposed for the legacy
            shim's convenience).
        apply_use_case_factory: Callable that returns a fresh
            :class:`LegacyUseCasePort` for ``apply_vacancies`` on each
            invocation.  Factored out because the legacy
            ``AppContainer.apply_to_vacancies_use_case(...)`` returns
            a *new* use case instance configured with the requested
            AI / email / system-prompt parameters.
        prepare_use_case_factory: Same shape as
            ``apply_use_case_factory``; not consumed by the new
            :class:`Api` (the legacy UI does not expose
            ``prepare_vacancies`` to the webview) but included in the
            context for forward-compatibility.
        presets: The :class:`PresetsManager` for the named- and
            last-used-preset round-trips.
        progress_sink: Callback invoked by :meth:`Api._send_progress`
            and the :class:`logging.Handler` that translates log
            records into webview progress events.
        auth_event_sink: Callback invoked by
            :meth:`Api._send_auth_event` to notify the webview of
            auth lifecycle changes (started / done / error).
        window: The pywebview window reference, set by
            :meth:`UiSlice.set_window` after the webview is created.
        get_me: Optional helper — :class:`Api.get_status` calls this
            to retrieve the current user.  Bound by the legacy shim
            to ``tool.get_me``; the VSA slice can replace it with a
            port adapter.  Defaults to a no-op stub.
        get_resumes: Same shape as :attr:`get_me` — used by
            :meth:`Api.get_resumes`.  Defaults to a no-op stub that
            returns an empty list.
        get_negotiations: Same shape — used by
            :meth:`Api.refresh_negotiations`.  Defaults to an empty
            iterator.
        clear_token: Optional helper — :meth:`Api.logout` and
            :meth:`Api.get_status` call this when the token is
            invalid.  Bound by the legacy shim to ``tool.api_client``
            attribute clears plus ``tool.config.save_token({})``.
    """

    api_client: HhApiClientPort
    config: ConfigPort
    storage: StoragePort
    apply_use_case_factory: Callable[..., LegacyUseCasePort]
    prepare_use_case_factory: Callable[..., LegacyUseCasePort]
    presets: Any
    progress_sink: Callable[[int, int, str], None]
    auth_event_sink: Callable[[str, str], None]
    window: Any | None = None
    # Optional helpers bound by the legacy shim; replaced by port
    # adapters in the VSA slice.
    get_me: Callable[[], Any] = field(
        default=lambda: {"first_name": "", "last_name": "", "email": ""}
    )
    get_resumes: Callable[[], list[dict[str, Any]]] = field(default=lambda: [])
    get_negotiations: Callable[..., Any] = field(
        default=lambda *a, **kw: iter(())
    )
    clear_token: Callable[[], None] = field(default=lambda: None)


__all__ = [
    "ConfigPort",
    "HhApiClientPort",
    "LegacyUseCasePort",
    "StoragePort",
    "UiApiContext",
]
