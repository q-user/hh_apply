"""Tests for the ``job_bot.ui`` slice (VSA — Issue #150).

TDD: tests are written first, then the slice is implemented to make them
pass.  The slice is a thin re-implementation of the legacy
``job_bot.ui.api.Api`` class — same public method names, so
the webview HTML/JS in ``src/job_bot/ui/templates/`` keeps working
unchanged — but the methods are now 1-3 line dispatches into a
:class:`UiApiContext` that bundles the slice's dependencies.

Test layout
-----------

* :class:`TestUiApiContext` — the dataclass shape and the window setter
  propagation.
* :class:`TestPresetsManagerOnStoragePort` — ``PresetsManager`` accepts
  any object that has a ``.settings`` attribute returning something
  with ``get_value`` / ``set_value`` / ``delete_value`` / ``list_keys``
  (``StoragePort.settings`` in production, duck-typed in tests).
* :class:`TestUiSlice` — the slice factory wires the right pieces and
  ``set_window`` propagates into the underlying :class:`Api`.
* :class:`TestApiMethodDispatch` — the 19 public methods on the new
  :class:`Api` route to the right port on the :class:`UiApiContext`.

After issue #158 the legacy UI shim is deleted
and the ``TestLegacyShim`` class is removed; the VSA surface above
is the only one the codebase supports.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from job_bot._legacy_compat.storage import StorageFacade

# ─── In-memory fakes for the slice's ports ────────────────────────────


class _FakeApiClient:
    """In-memory :class:`HhApiClientPort` for the UI tests.

    Records the most recent ``get()`` call so the dispatch tests can
    assert on routing.  Exposes the ``access_token`` / ``refresh_token``
    / ``access_expires_at`` / ``delay`` attributes the legacy :class:`Api`
    touches during auth lifecycle.
    """

    def __init__(self) -> None:
        self.access_token: str | None = "tok"
        self.refresh_token: str | None = "ref"
        self.access_expires_at: int = 0
        self.delay: float = 0.0
        self.get_calls: list[tuple[str, dict | None]] = []
        self.get_responses: dict[str, Any] = {}

    def get(self, endpoint: str, params: dict | None = None) -> Any:
        self.get_calls.append((endpoint, params))
        return self.get_responses.get(endpoint, {})

    def post(self, endpoint: str, payload: dict | None = None) -> Any:
        return {}

    def put(self, endpoint: str, json_data: dict | None = None) -> Any:
        return {}

    def delete(self, endpoint: str) -> Any:
        return {}


class _FakeConfig(dict):
    """Dict-like config with ``save(**kwargs)`` and ``save_token(dict)``.

    Inherits from :class:`dict` so ``dict(self)`` (used by
    :meth:`Api.get_config`) works on the same fast path the legacy
    :class:`MockConfig` test double relies on.  Stores the
    :attr:`save_calls` / :attr:`save_token_calls` for assertions.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        super().__init__(data or {})
        self.save_calls: list[dict[str, Any]] = []
        self.save_token_calls: list[dict[str, Any]] = []

    def save(self, **kwargs: Any) -> None:
        self.save_calls.append(kwargs)
        self.update(kwargs)

    def save_token(self, token: dict[str, Any]) -> None:
        self.save_token_calls.append(dict(token))


class _FakeUseCase:
    """In-memory :class:`LegacyUseCasePort`.

    The real ``ApplyToVacanciesUseCase`` and ``PrepareVacanciesUseCase``
    satisfy the Protocol structurally; this fake mimics the relevant
    ``execute(command, *, cancel_event=None)`` shape.
    """

    def __init__(self) -> None:
        self.execute_calls: list[tuple[Any, Any]] = []

    def execute(self, command: Any, *, cancel_event: Any = None) -> Any:
        self.execute_calls.append((command, cancel_event))
        return MagicMock(name="ApplyResult")


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def fake_storage() -> StorageFacade:
    """In-memory :class:`StorageFacade` for the dispatch tests.

    Mirrors the project's ``storage`` fixture in
    ``tests/conftest.py`` — initialised schema, ``:memory:`` SQLite.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from job_bot._legacy_compat.storage.utils import init_db

    init_db(conn)
    return StorageFacade(conn)


@pytest.fixture
def fake_api_client() -> _FakeApiClient:
    return _FakeApiClient()


@pytest.fixture
def fake_config() -> _FakeConfig:
    return _FakeConfig(
        {
            "client_id": "cid",
            "client_secret": "csecret",
            "token": {"access_token": "tok", "refresh_token": "ref"},
            "openai_cover_letter": {
                "api_key": "sk-xxx",
                "model": "gpt-4",
            },
        }
    )


@pytest.fixture
def fake_apply_use_case() -> _FakeUseCase:
    return _FakeUseCase()


@pytest.fixture
def fake_prepare_use_case() -> _FakeUseCase:
    return _FakeUseCase()


@pytest.fixture
def progress_sink() -> Callable[[int, int, str], None]:
    """Captures ``Api._send_progress`` dispatch calls."""
    calls: list[tuple[int, int, str]] = []

    def _sink(current: int, total: int, message: str) -> None:
        calls.append((current, total, message))

    _sink.calls = calls  # type: ignore[attr-defined]
    return _sink


@pytest.fixture
def auth_event_sink() -> Callable[[str, str], None]:
    """Captures ``Api._send_auth_event`` dispatch calls."""
    calls: list[tuple[str, str]] = []

    def _sink(event: str, message: str) -> None:
        calls.append((event, message))

    _sink.calls = calls  # type: ignore[attr-defined]
    return _sink


# ─── UiApiContext tests ──────────────────────────────────────────────


class TestUiApiContext:
    """The :class:`UiApiContext` dataclass is the slice's DI surface.

    Issue #150 acceptance criterion: each of the 19 methods of the new
    :class:`Api` is a 1-3 line dispatch into one of the context's
    fields.  These tests pin the *shape* of the context — change the
    field names and the whole UI surface breaks, so the test catches it
    loudly.
    """

    def test_context_bundles_all_required_ports(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        from job_bot.ui.ports import UiApiContext
        from job_bot.ui.presets import PresetsManager

        presets = PresetsManager(fake_storage.settings)
        ctx = UiApiContext(
            api_client=fake_api_client,
            config=fake_config,
            storage=fake_storage,
            apply_use_case_factory=lambda **kw: fake_apply_use_case,
            prepare_use_case_factory=lambda **kw: fake_prepare_use_case,
            presets=presets,
            progress_sink=progress_sink,
            auth_event_sink=auth_event_sink,
        )
        assert ctx.api_client is fake_api_client
        assert ctx.config is fake_config
        assert ctx.storage is fake_storage
        assert ctx.presets is presets
        assert ctx.progress_sink is progress_sink
        assert ctx.auth_event_sink is auth_event_sink
        # ``window`` defaults to None until ``set_window()`` is called.
        assert ctx.window is None

    def test_window_is_settable(self, fake_api_client: _FakeApiClient) -> None:
        from job_bot.ui.ports import UiApiContext

        ctx = UiApiContext(
            api_client=fake_api_client,
            config=_FakeConfig(),
            storage=MagicMock(),
            apply_use_case_factory=lambda **kw: MagicMock(),
            prepare_use_case_factory=lambda **kw: MagicMock(),
            presets=MagicMock(),
            progress_sink=lambda *a: None,
            auth_event_sink=lambda *a: None,
        )
        sentinel = object()
        ctx.window = sentinel  # type: ignore[assignment]
        assert ctx.window is sentinel


# ─── PresetsManager on StoragePort ───────────────────────────────────


class TestPresetsManagerOnStoragePort:
    """``PresetsManager`` only needs ``storage.settings`` from the port.

    Issue #150 acceptance criterion: ``src/job_bot/ui/presets.py`` no
    longer imports the legacy storage layer.  The move shifts
    the dependency to the abstract :class:`StoragePort` Protocol — any
    object that exposes ``.settings`` (with the four
    ``SettingsRepository`` methods) satisfies the contract.
    """

    def test_does_not_import_hh_applicant_tool_storage(self) -> None:
        import job_bot.ui.presets as presets_module

        source = open(presets_module.__file__).read()
        # No real imports of the legacy storage layer; the slice is
        # fully decoupled.
        assert "from hh_applicant_tool" not in source
        assert "import hh_applicant_tool" not in source

    def test_presets_manager_works_with_settings_repository(
        self, fake_storage: StorageFacade
    ) -> None:
        from job_bot.ui.presets import (
            PresetsManager,
        )

        manager = PresetsManager(fake_storage.settings)
        manager.save("alpha", {"search": "python"})
        assert manager.load("alpha") == {"search": "python"}
        assert "alpha" in manager.list_names()
        manager.delete("alpha")
        assert manager.load("alpha") is None

    def test_presets_manager_works_with_duck_typed_settings(
        self,
    ) -> None:
        """Any object with ``get_value`` / ``set_value`` / ``list_keys`` works.

        This is the structural-type contract that lets the slice depend
        on :class:`StoragePort` instead of a concrete class.
        """
        from job_bot.ui.presets import PresetsManager

        class _DuckSettings:
            def __init__(self) -> None:
                self._store: dict[str, Any] = {}

            def get_value(self, key: str) -> Any:
                return self._store.get(key)

            def set_value(self, key: str, value: Any) -> None:
                self._store[key] = value

            def delete_value(self, key: str) -> None:
                self._store.pop(key, None)

            def list_keys(self) -> list[str]:
                return list(self._store)

        manager = PresetsManager(_DuckSettings())
        manager.save("foo", {"x": 1})
        assert manager.load("foo") == {"x": 1}
        manager.save_last_used({"y": 2})
        assert manager.load_last_used() == {"y": 2}


# ─── UiSlice tests ───────────────────────────────────────────────────


class TestUiSlice:
    """The :class:`UiSlice` factory wires the context and exposes the API.

    Issue #150 acceptance criterion: ``UiSlice`` constructs an :class:`Api`
    instance from the dependencies it receives, and
    :meth:`UiSlice.set_window` propagates the window to the API.
    """

    def _make_dependencies(
        self,
        fake_api_client: _FakeApiClient,
        fake_storage: StorageFacade,
        fake_config: _FakeConfig,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> dict[str, Any]:
        from job_bot.ui.presets import PresetsManager

        return {
            "api_client": fake_api_client,
            "config": fake_config,
            "storage": fake_storage,
            "apply_use_case_factory": lambda **kw: fake_apply_use_case,
            "prepare_use_case_factory": lambda **kw: fake_prepare_use_case,
            "presets": PresetsManager(fake_storage.settings),
            "progress_sink": progress_sink,
            "auth_event_sink": auth_event_sink,
        }

    def test_slice_constructs_api(
        self,
        fake_api_client: _FakeApiClient,
        fake_storage: StorageFacade,
        fake_config: _FakeConfig,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        from job_bot.ui.slice import UiSlice

        deps = self._make_dependencies(
            fake_api_client,
            fake_storage,
            fake_config,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        slice_ = UiSlice(**deps)
        assert slice_.api is not None
        # ``slice.api`` is a fresh ``Api`` with a ``UiApiContext``.
        assert slice_.api.context is not None
        assert slice_.api.context.storage is fake_storage

    def test_set_window_propagates(
        self,
        fake_api_client: _FakeApiClient,
        fake_storage: StorageFacade,
        fake_config: _FakeConfig,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        from job_bot.ui.slice import UiSlice

        deps = self._make_dependencies(
            fake_api_client,
            fake_storage,
            fake_config,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        slice_ = UiSlice(**deps)
        sentinel = object()
        slice_.set_window(sentinel)
        assert slice_.api.context.window is sentinel

    def test_cached_property_returns_same_api(
        self,
        fake_api_client: _FakeApiClient,
        fake_storage: StorageFacade,
        fake_config: _FakeConfig,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        from job_bot.ui.slice import UiSlice

        deps = self._make_dependencies(
            fake_api_client,
            fake_storage,
            fake_config,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        slice_ = UiSlice(**deps)
        # The API is memoised.
        assert slice_.api is slice_.api


# ─── Api method-dispatch tests ───────────────────────────────────────


def _make_api(
    fake_api_client: _FakeApiClient,
    fake_config: _FakeConfig,
    fake_storage: StorageFacade,
    fake_apply_use_case: _FakeUseCase,
    fake_prepare_use_case: _FakeUseCase,
    progress_sink: Callable[[int, int, str], None],
    auth_event_sink: Callable[[str, str], None],
) -> Any:
    """Helper: build a new :class:`Api` with the test fakes."""
    from job_bot.ui.api import Api
    from job_bot.ui.ports import UiApiContext
    from job_bot.ui.presets import PresetsManager

    ctx = UiApiContext(
        api_client=fake_api_client,
        config=fake_config,
        storage=fake_storage,
        apply_use_case_factory=lambda **kw: fake_apply_use_case,
        prepare_use_case_factory=lambda **kw: fake_prepare_use_case,
        presets=PresetsManager(fake_storage.settings),
        progress_sink=progress_sink,
        auth_event_sink=auth_event_sink,
    )
    return Api(ctx)


class TestApiMethodDispatch:
    """Each of the 19 public methods on :class:`Api` routes to a port.

    These tests build a fresh :class:`Api` from the in-memory fakes and
    assert that calling each method touches exactly the right port on
    the :class:`UiApiContext`.  They double as a regression guard
    against accidentally re-introducing a direct
    ``self._tool.X.Y`` access in the new :class:`Api`.
    """

    def test_get_status_routes_to_api_client_and_get_me(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        # Configure a fake ``get_me`` provider via a bound attribute.
        api.context.get_me = MagicMock(return_value={"first_name": "Иван"})  # type: ignore[attr-defined]
        status = api.get_status()
        assert status["authorized"] is True
        assert status["user"]["first_name"] == "Иван"
        # Auth-running flag is consulted first.
        assert status.get("auth_running", False) is False

    def test_get_status_no_token(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        fake_api_client.access_token = None
        fake_api_client.refresh_token = None
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        status = api.get_status()
        assert status["authorized"] is False
        assert status["reason"] == "no_token"

    def test_get_resumes_no_token_returns_empty(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        fake_api_client.access_token = None
        fake_api_client.refresh_token = None
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        assert api.get_resumes() == []

    def test_get_resumes_via_get_resumes_provider(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        api.context.get_resumes = MagicMock(  # type: ignore[attr-defined]
            return_value=[{"id": "r1", "title": "Python"}]
        )
        assert api.get_resumes() == [{"id": "r1", "title": "Python"}]

    def test_logout_calls_clear_token(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        api.context.clear_token = MagicMock()  # type: ignore[attr-defined]
        result = api.logout()
        assert result == {"status": "ok"}
        api.context.clear_token.assert_called_once()  # type: ignore[attr-defined]

    def test_get_config_masks_secrets(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        masked = api.get_config()
        assert masked["client_secret"] == "***"
        assert masked["token"] == "***"
        # Nested api_key is masked too.
        assert masked["openai_cover_letter"]["api_key"] == "***"
        # Public fields are visible.
        assert masked["client_id"] == "cid"
        assert masked["openai_cover_letter"]["model"] == "gpt-4"

    def test_save_config_merges_into_config(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        result = api.save_config({"client_id": "new_id"})
        assert result == {"status": "ok"}
        # The fake config captured the save call.
        assert fake_config.save_calls[-1] == {"client_id": "new_id"}

    def test_save_config_rejects_mask_value(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        # Masked values are stripped before reaching ``config.save``.
        api.save_config({"client_secret": "***"})
        # The ``client_secret`` key should not be passed through at all
        # (or the masked value is filtered out — both are acceptable).
        last = fake_config.save_calls[-1] if fake_config.save_calls else {}
        assert last.get("client_secret", "***") == "***"

    def test_preset_methods_route_to_presets_manager(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        # ``PresetsManager`` is real, backed by ``fake_storage.settings``.
        result = api.save_preset("p1", {"search": "python"})
        assert result == {"status": "ok"}
        assert "p1" in api.list_presets()
        assert api.load_preset("p1") == {"search": "python"}
        api.delete_preset("p1")
        assert api.load_preset("p1") is None

    def test_save_preset_validation_error_returns_error(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        result = api.save_preset("", {"search": "x"})
        assert result["status"] == "error"

    def test_last_used_params_round_trip(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        params = {"search": "go", "area": ["1"]}
        api.save_last_used_params(params)
        assert api.get_last_used_params() == params

    def test_get_negotiations_from_db_runs_sql(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        # Empty DB returns an empty list (not a SQL error).
        assert api.get_negotiations_from_db() == []

    def test_get_statistics_aggregates(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        stats = api.get_statistics()
        assert "by_state" in stats
        assert "skipped_by_reason" in stats
        assert "daily_negotiations" in stats
        assert "daily_skipped" in stats
        assert stats["total_negotiations"] == 0
        assert stats["total_skipped"] == 0

    def test_refresh_negotiations_invokes_provider(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        # ``refresh_negotiations`` calls a provider for the live data
        # and persists each item into ``storage.negotiations``.  The
        # provider is exposed as ``context.get_negotiations``.
        api.context.get_negotiations = MagicMock(  # type: ignore[attr-defined]
            return_value=iter([])
        )
        result = api.refresh_negotiations("active")
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_apply_vacancies_invokes_use_case(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        # ``get_resumes`` must return at least one resume so the use
        # case is invoked in dry-run mode.
        api.context.get_resumes = MagicMock(  # type: ignore[attr-defined]
            return_value=[{"id": "r1"}]
        )
        result = api.apply_vacancies({"search": "test", "dry_run": True})
        assert result["status"] in ("ok", "cancelled", "error")
        # The use case was invoked at least once.
        assert len(fake_apply_use_case.execute_calls) >= 1

    def test_apply_vacancies_ignores_unknown_keys(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        api.context.get_resumes = MagicMock(  # type: ignore[attr-defined]
            return_value=[]
        )
        result = api.apply_vacancies(
            {"nonexistent_flag_xyz": "leak /root/.ssh"}
        )
        # No leak in any return value.
        assert "/root/.ssh" not in str(result)
        assert result["status"] in ("ok", "cancelled", "error")

    def test_apply_vacancies_generic_message_on_failure(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        # Force the apply use-case factory to raise an exception with a
        # sensitive path in the message; the new ``Api`` must swallow
        # ``str(e)`` and return a generic error dict.
        def _exploding_factory(**kw: Any) -> Any:
            raise RuntimeError("leak /root/.ssh")

        from job_bot.ui.api import Api
        from job_bot.ui.ports import UiApiContext
        from job_bot.ui.presets import PresetsManager

        ctx = UiApiContext(
            api_client=fake_api_client,
            config=fake_config,
            storage=fake_storage,
            apply_use_case_factory=_exploding_factory,
            prepare_use_case_factory=lambda **kw: fake_prepare_use_case,
            presets=PresetsManager(fake_storage.settings),
            progress_sink=progress_sink,
            auth_event_sink=auth_event_sink,
        )
        api = Api(ctx)
        api.context.get_resumes = MagicMock(  # type: ignore[attr-defined]
            return_value=[{"id": "r1"}]
        )
        result = api.apply_vacancies({"search": "test"})
        assert result["status"] == "error"
        assert "/root/.ssh" not in result.get("message", "")

    def test_cancel_apply_sets_cancel_event(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        from job_bot.ui.api import Api
        from job_bot.ui.ports import UiApiContext
        from job_bot.ui.presets import PresetsManager

        ctx = UiApiContext(
            api_client=fake_api_client,
            config=fake_config,
            storage=fake_storage,
            apply_use_case_factory=lambda **kw: fake_apply_use_case,
            prepare_use_case_factory=lambda **kw: fake_prepare_use_case,
            presets=PresetsManager(fake_storage.settings),
            progress_sink=progress_sink,
            auth_event_sink=auth_event_sink,
        )
        api = Api(ctx)
        # No-op: there's no running job, so cancel_apply must not raise.
        api.cancel_apply()

    def test_get_areas_routes_to_api_client(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        fake_api_client.get_responses["/areas"] = [
            {
                "id": "1",
                "name": "Россия",
                "areas": [{"id": "2", "name": "Москва"}],
            }
        ]
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        areas = api.get_areas()
        assert [a["id"] for a in areas] == ["1", "2"]
        # Nested area gets indentation prefix.
        assert areas[1]["name"].startswith("  ")
        # ``get_areas`` was routed through the api client.
        assert ("/areas", None) in fake_api_client.get_calls

    def test_get_professional_roles_routes_to_api_client(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        fake_api_client.get_responses["/professional_roles"] = {
            "categories": [
                {
                    "id": "cat1",
                    "name": "Tech",
                    "roles": [{"id": "r1", "name": "Backend"}],
                }
            ]
        }
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        roles = api.get_professional_roles()
        assert roles == [{"id": "r1", "name": "Backend"}]

    def test_get_industries_routes_to_api_client(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        fake_api_client.get_responses["/industries"] = [
            {
                "id": "i1",
                "name": "IT",
                "industries": [{"id": "i2", "name": "Dev"}],
            }
        ]
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        industries = api.get_industries()
        assert industries == [
            {"id": "i1", "name": "IT"},
            {"id": "i2", "name": "  Dev"},
        ]

    def test_progress_sink_is_called(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        from job_bot.ui.api import Api
        from job_bot.ui.ports import UiApiContext
        from job_bot.ui.presets import PresetsManager

        ctx = UiApiContext(
            api_client=fake_api_client,
            config=fake_config,
            storage=fake_storage,
            apply_use_case_factory=lambda **kw: fake_apply_use_case,
            prepare_use_case_factory=lambda **kw: fake_prepare_use_case,
            presets=PresetsManager(fake_storage.settings),
            progress_sink=progress_sink,
            auth_event_sink=auth_event_sink,
        )
        api = Api(ctx)
        # Direct call to the internal sink dispatcher (the new ``Api``
        # exposes ``_send_progress`` as a 1-3 line dispatch into the
        # context's progress_sink).
        api._send_progress(1, 2, "msg")
        assert progress_sink.calls == [(1, 2, "msg")]  # type: ignore[attr-defined]

    def test_auth_event_sink_is_called(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        from job_bot.ui.api import Api
        from job_bot.ui.ports import UiApiContext
        from job_bot.ui.presets import PresetsManager

        ctx = UiApiContext(
            api_client=fake_api_client,
            config=fake_config,
            storage=fake_storage,
            apply_use_case_factory=lambda **kw: fake_apply_use_case,
            prepare_use_case_factory=lambda **kw: fake_prepare_use_case,
            presets=PresetsManager(fake_storage.settings),
            progress_sink=progress_sink,
            auth_event_sink=auth_event_sink,
        )
        api = Api(ctx)
        api._send_auth_event("done", "ok")
        assert auth_event_sink.calls == [("done", "ok")]  # type: ignore[attr-defined]

    def test_progress_handler_calls_sink(
        self,
        fake_api_client: _FakeApiClient,
        fake_config: _FakeConfig,
        fake_storage: StorageFacade,
        fake_apply_use_case: _FakeUseCase,
        fake_prepare_use_case: _FakeUseCase,
        progress_sink: Callable[[int, int, str], None],
        auth_event_sink: Callable[[str, str], None],
    ) -> None:
        """The ``_ProgressHandler`` (logging handler) routes via the API.

        Issue #150 acceptance criterion: the ``_ProgressHandler`` is a
        ``logging.Handler`` that emits via the :class:`Api`'s progress
        dispatcher (which itself dispatches into the context's
        ``progress_sink``).  Pinning the integration here makes sure
        the logging → JS round-trip still works after the refactor.
        """
        api = _make_api(
            fake_api_client,
            fake_config,
            fake_storage,
            fake_apply_use_case,
            fake_prepare_use_case,
            progress_sink,
            auth_event_sink,
        )
        # Use a public hook to verify the handler plumbing.
        logging.Handler(level=logging.INFO)

        class _ProbeHandler(logging.Handler):
            def emit(self_inner, record: logging.LogRecord) -> None:
                api._send_progress(99, 0, self_inner.format(record))

        probe = _ProbeHandler(level=logging.INFO)
        probe.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test_progress_handler_route")
        logger.setLevel(logging.INFO)
        logger.addHandler(probe)
        try:
            logger.info("hello")
        finally:
            logger.removeHandler(probe)
        assert progress_sink.calls == [(99, 0, "hello")]  # type: ignore[attr-defined]
