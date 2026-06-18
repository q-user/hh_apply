"""Tests for the shared secrets-backends module (issue #206).

The :mod:`job_bot.shared.secrets` package exposes a ``SecretsManager``
facade that picks a ``SecretsBackend`` at construction time. Three
backends ship in the box:

* :class:`EnvBackend` -- reads from ``os.environ``. The default and the
  one the rest of the codebase has been using implicitly so far.
* :class:`KeyringBackend` -- reads / writes via the ``keyring`` PyPI
  package, which delegates to the OS keyring (macOS Keychain, Linux
  Secret Service, Windows Credential Vault).
* :class:`VaultBackend` -- placeholder for HashiCorp Vault. The
  interface allows a future drop-in implementation; ``get`` / ``set``
  raise :class:`NotImplementedError` until then.

The tests cover:

* :class:`EnvBackend` round-trips through ``os.environ`` and refuses
  writes (the env is a read-only source of truth for the rest of the
  code).
* :class:`KeyringBackend` round-trips against a fake ``keyring`` module
  injected via ``sys.modules`` (the real OS keyring is hard to use in
  tests), and raises an :class:`ImportError` with an install hint when
  the ``keyring`` package is missing.
* :class:`SecretsManager` dispatches to its backend, builds itself from
  a config dict via :meth:`from_config`, returns the supplied default
  when a secret is missing, and raises on an unknown backend name.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

from job_bot.shared.secrets import (
    EnvBackend,
    KeyringBackend,
    SecretsBackend,
    SecretsManager,
    VaultBackend,
)
from job_bot.shared.secrets.backend import SecretsBackend as SecretsBackendClass
from job_bot.shared.secrets.errors import (
    SecretBackendUnavailableError,
    SecretNotFoundError,
)
from job_bot.shared.secrets.manager import SecretsManager as ManagerClass

# ─── fakes / stubs ─────────────────────────────────────────────────────


class _FakeBackend:
    """Minimal in-memory :class:`SecretsBackend` for dispatch tests.

    Uses the runtime-checkable Protocol so the test fails loudly if the
    public surface ever drifts away from the test fake. The
    ``__setitem__``-style dict keeps assertion code small.
    """

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._store = dict(values or {})
        self.set_calls: list[tuple[str, str]] = []

    def get(self, name: str) -> str | None:
        return self._store.get(name)

    def set(self, name: str, value: str) -> None:
        self.set_calls.append((name, value))
        self._store[name] = value


class _FakeKeyringModule(ModuleType):
    """In-memory fake of the ``keyring`` PyPI package.

    The real ``keyring`` module exposes ``get_password`` and
    ``set_password`` (each taking ``(service, name)`` and
    ``(service, name, value)`` respectively). Mirroring that surface
    keeps the :class:`KeyringBackend` adapter thin and the fake drop-in.
    """

    def __init__(self) -> None:
        super().__init__("keyring_fake_for_secrets_tests")
        self._store: dict[tuple[str, str], str] = {}
        self.last_service: str | None = None

    def get_password(self, service: str, name: str) -> str | None:
        self.last_service = service
        return self._store.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.last_service = service
        self._store[(service, name)] = value


# ─── EnvBackend ────────────────────────────────────────────────────────


def test_env_backend_returns_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """``EnvBackend().get`` must read from ``os.environ``."""
    monkeypatch.setenv("HH_TEST_SECRET", "s3cr3t")
    backend = EnvBackend()

    assert backend.get("HH_TEST_SECRET") == "s3cr3t"


def test_env_backend_returns_none_for_missing() -> None:
    """An unset env var must round-trip as ``None`` (never ``""``)."""
    backend = EnvBackend()

    assert backend.get("HH_DEFINITELY_UNSET_VAR_XYZ_206") is None


def test_env_backend_set_is_rejected() -> None:
    """The env backend is a read-only source; ``set`` must fail loudly.

    Refusing writes avoids a class of bugs where a caller thinks the
    secret is persisted (it would only live in the current process's
    env) and the next process restart silently loses it. Callers that
    want a writable store should use :class:`KeyringBackend`.
    """
    backend = EnvBackend()

    with pytest.raises(SecretBackendUnavailableError):
        backend.set("HH_WHATEVER", "x")


def test_env_backend_satisfies_protocol() -> None:
    """The env backend must be a structural instance of the Protocol."""
    backend = EnvBackend()

    assert isinstance(backend, SecretsBackend)


# ─── KeyringBackend ────────────────────────────────────────────────────


def test_keyring_backend_round_trip_with_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A set / get cycle against the fake ``keyring`` module round-trips.

    The fake is injected into ``sys.modules`` so the lazy import inside
    :class:`KeyringBackend` picks it up without a real keyring
    install. The ``service`` argument is propagated so an operator can
    tell two apps sharing the same OS keyring apart.
    """
    fake = _FakeKeyringModule()
    monkeypatch.setitem(sys.modules, "keyring", fake)

    backend = KeyringBackend(service_name="hh-applicant-tool-tests")
    backend.set("FOO", "bar")

    assert fake.last_service == "hh-applicant-tool-tests"
    assert backend.get("FOO") == "bar"
    # A second read of an unknown key is ``None`` (not an error), so
    # callers can use ``manager.get(name, default=...)`` uniformly
    # across backends.
    assert backend.get("NOT_THERE") is None


def test_keyring_backend_satisfies_protocol() -> None:
    """The keyring backend must be a structural instance of the Protocol.

    The fake module is injected so the import succeeds; the structural
    check itself does not need a real ``get``/``set`` round-trip (we
    just verified that one above).
    """
    fake = _FakeKeyringModule()
    sys.modules["keyring"] = fake
    try:
        backend = KeyringBackend(service_name="hh-applicant-tool-tests")
    finally:
        del sys.modules["keyring"]

    assert isinstance(backend, SecretsBackend)


def test_keyring_backend_import_error_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``keyring`` is not installed, raise ``ImportError`` with hint.

    Setting ``sys.modules["keyring"] = None`` is the canonical
    way to make the next ``import keyring`` raise :class:`ImportError`
    (CPython treats ``None`` as a sentinel for "this module is
    unimportable"). The error message must mention the optional extra
    so users can recover via ``uv pip install -e .[secrets]``.
    """
    monkeypatch.setitem(sys.modules, "keyring", None)

    with pytest.raises(ImportError, match=r"keyring"):
        KeyringBackend(service_name="hh-applicant-tool-tests")


# ─── VaultBackend (placeholder) ────────────────────────────────────────


def test_vault_backend_get_raises_not_implemented() -> None:
    """The vault backend is a placeholder; ``get`` must fail loudly.

    Failing fast beats silently returning ``None``: a missing Vault
    implementation must not be confused with a missing secret, and the
    issue text marks the Vault impl as out of scope for this PR.
    """
    backend = VaultBackend()

    with pytest.raises(NotImplementedError):
        backend.get("WHATEVER")


def test_vault_backend_set_raises_not_implemented() -> None:
    """``VaultBackend.set`` is symmetric with ``get`` and also raises."""
    backend = VaultBackend()

    with pytest.raises(NotImplementedError):
        backend.set("WHATEVER", "value")


def test_vault_backend_satisfies_protocol() -> None:
    """Even a placeholder backend must satisfy the Protocol shape."""
    backend = VaultBackend()

    assert isinstance(backend, SecretsBackend)


# ─── SecretsManager ────────────────────────────────────────────────────


def test_manager_dispatches_to_backend() -> None:
    """``SecretsManager.get`` must forward to its backend verbatim."""
    fake = _FakeBackend({"ALPHA": "one"})
    manager = SecretsManager(backend=fake)

    assert manager.get("ALPHA") == "one"
    assert manager.get("MISSING") is None


def test_manager_set_dispatches_to_backend() -> None:
    """``SecretsManager.set`` must forward to the backend and remember it."""
    fake = _FakeBackend()
    manager = SecretsManager(backend=fake)

    manager.set("BETA", "two")

    assert fake.set_calls == [("BETA", "two")]
    assert manager.get("BETA") == "two"


def test_manager_from_config_env() -> None:
    """``from_config({\"secrets\": {\"backend\": \"env\"}})`` wires an env backend.

    The factory is the canonical entry point for the AppContainer
    wiring -- a misspelled backend name must surface as a
    :class:`ValueError` so the container fails fast at startup.
    """
    manager = SecretsManager.from_config({"secrets": {"backend": "env"}})

    assert isinstance(manager._backend, EnvBackend)  # noqa: SLF001 -- internal check


def test_manager_from_config_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``from_config({\"secrets\": {\"backend\": \"keyring\"}})`` wires a keyring backend."""
    fake = _FakeKeyringModule()
    monkeypatch.setitem(sys.modules, "keyring", fake)

    manager = SecretsManager.from_config(
        {"secrets": {"backend": "keyring", "service_name": "test-svc"}}
    )

    assert isinstance(manager._backend, KeyringBackend)  # noqa: SLF001
    # Verify the service name flowed through to the backend, so the
    # same dict that wires the manager also determines which OS
    # keyring service the calls target.
    manager.set("X", "y")
    assert fake.last_service == "test-svc"


def test_manager_from_config_vault() -> None:
    """``from_config({\"secrets\": {\"backend\": \"vault\"}})`` wires a vault placeholder."""
    manager = SecretsManager.from_config({"secrets": {"backend": "vault"}})

    assert isinstance(manager._backend, VaultBackend)  # noqa: SLF001


def test_manager_from_config_unknown_raises() -> None:
    """An unknown backend name must raise ``ValueError`` at build time.

    Failing fast at startup keeps a typo from surfacing as a
    mysterious ``None`` for every secret the rest of the code reads.
    """
    with pytest.raises(ValueError, match=r"vaults?"):
        SecretsManager.from_config({"secrets": {"backend": "vaults"}})


def test_manager_from_config_default_is_env() -> None:
    """When no backend is configured, default to ``env`` (zero-config path).

    Backwards-compat is the whole point of this module -- users on the
    current env-var workflow keep working without touching their
    config file or the ``HH_SECRETS_BACKEND`` env var.
    """
    manager = SecretsManager.from_config({})

    assert isinstance(manager._backend, EnvBackend)  # noqa: SLF001


def test_manager_secret_not_found_returns_default() -> None:
    """``manager.get(name, default=...)`` returns the default when missing."""
    fake = _FakeBackend()
    manager = SecretsManager(backend=fake)

    assert manager.get("MISSING", default="fallback") == "fallback"


def test_manager_secret_not_found_returns_none_without_default() -> None:
    """``manager.get(name)`` returns ``None`` when missing and no default."""
    fake = _FakeBackend()
    manager = SecretsManager(backend=fake)

    assert manager.get("MISSING") is None


# ─── module surface ────────────────────────────────────────────────────


def test_secrets_module_public_surface() -> None:
    """The package's ``__all__`` re-exports the public surface.

    A new collaborator should be able to ``from job_bot.shared.secrets
    import SecretsManager`` (and friends) without reaching into the
    sub-modules. The test pins the names so a rename in the package
    forces a test failure here, not a silent breaking change at the
    call sites.
    """
    import job_bot.shared.secrets as pkg

    expected = {
        "EnvBackend",
        "KeyringBackend",
        "SecretsBackend",
        "SecretsManager",
        "VaultBackend",
        "SecretNotFoundError",
        "SecretBackendUnavailableError",
    }
    assert expected.issubset(set(pkg.__all__))
    for name in expected:
        assert hasattr(pkg, name), name


def test_errors_are_distinct_exceptions() -> None:
    """``SecretNotFoundError`` and ``SecretBackendUnavailableError`` differ.

    Callers may want to handle "secret missing" (caller can substitute
    a default) differently from "backend unreachable" (caller should
    fail fast). Keeping them as distinct types preserves that
    flexibility.
    """
    assert issubclass(SecretNotFoundError, Exception)
    assert issubclass(SecretBackendUnavailableError, Exception)
    assert SecretNotFoundError is not SecretBackendUnavailableError


# ─── aliases / module attribute sanity ────────────────────────────────


def test_re_exported_classes_match_submodule_classes() -> None:
    """``SecretsManager`` and friends must be the *same* class object
    as their sub-module definition.

    A common refactor mistake is to re-export a factory function under
    the class name, breaking ``isinstance`` checks. Pin the identity
    here so the public surface stays honest.
    """
    from job_bot.shared.secrets import (
        env_backend as env_mod,
    )
    from job_bot.shared.secrets import (
        keyring_backend as keyring_mod,
    )
    from job_bot.shared.secrets import (
        vault_backend as vault_mod,
    )

    assert SecretsManager is ManagerClass
    assert SecretsBackend is SecretsBackendClass
    assert EnvBackend is env_mod.EnvBackend
    assert KeyringBackend is keyring_mod.KeyringBackend
    assert VaultBackend is vault_mod.VaultBackend


def test_module_is_importable() -> None:
    """``importlib.reload`` must succeed -- no import-time side effects.

    The :class:`KeyringBackend` must lazy-import ``keyring`` so a
    missing optional dep does not break ``import job_bot.shared.secrets``.
    A successful reload of the package is a cheap proxy for that.
    """
    import job_bot.shared.secrets as pkg

    importlib.reload(pkg)
    # And the manager's ``from_config`` still works after a reload.
    mgr = SecretsManager.from_config({})
    assert isinstance(mgr._backend, EnvBackend)  # noqa: SLF001


# ─── call-site migration (issue #206) ────────────────────────────────


def test_config_handler_load_uses_injected_manager(
    tmp_path: Path,
) -> None:
    """``ConfigHandler`` must read ``HH_PROFILE_ID`` via the manager.

    Migration target: ``ConfigHandler.load`` used to call
    ``os.environ.get("HH_PROFILE_ID")`` directly. With the
    ``secrets_manager`` injection, a deployment that opts in to a
    non-env backend can serve the same key from a different store.
    """
    import json

    from job_bot.config_auth.handlers.config_handler import ConfigHandler

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "hh": {"client_id": "x", "client_secret": "y"},
                "profiles": {"work": {"client_id": "w", "client_secret": "z"}},
            }
        )
    )
    fake = _FakeBackend({"HH_PROFILE_ID": "work"})

    handler = ConfigHandler(secrets_manager=SecretsManager(backend=fake))
    config = handler.load(config_path)

    assert config.active_profile == "work"


def test_config_handler_default_uses_env_backend(tmp_path: Path) -> None:
    """``ConfigHandler()`` with no args falls back to ``EnvBackend``.

    Backwards-compat: callers that do not pass a manager get the same
    behaviour as before issue #206.
    """
    from job_bot.config_auth.handlers.config_handler import ConfigHandler

    handler = ConfigHandler()
    assert isinstance(handler._secrets._backend, EnvBackend)  # noqa: SLF001


def test_telegram_transport_default_config_path_uses_manager() -> None:
    """``TelegramTransport`` reads ``HH_PROFILE_ID`` via the manager.

    The transport stores the manager on ``self._secrets``; the
    :meth:`_default_config_path` classmethod uses a fresh default
    manager, but the constructor injection is what the
    :class:`AppContainer` does in production. We assert the
    constructor injection path forwards the manager correctly.
    """
    from job_bot.telegram_bot.telegram_transport import (
        TelegramTransport,
        TelegramTransportConfig,
    )

    fake = _FakeBackend({"HH_PROFILE_ID": "issue-206"})
    transport = TelegramTransport(
        config=TelegramTransportConfig(bot_token="dummy"),
        secrets_manager=SecretsManager(backend=fake),
    )
    # The manager is forwarded; a ``get`` against the same key
    # returns the value the fake backend was seeded with.
    assert transport._secrets is not None  # noqa: SLF001
    assert transport._secrets.get("HH_PROFILE_ID") == "issue-206"  # noqa: SLF001


# ─── CLI flag (issue #206) ────────────────────────────────────────


def test_cli_flag_secrets_backend_sets_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``--secrets-backend`` flag translates to ``HH_SECRETS_BACKEND``.

    We exercise the parser directly: the container's ``_build_parser``
    adds the flag, and the dispatch body in ``AppContainer.run`` sets
    the env var before any slice accessor runs. The test asserts the
    *first* part -- the flag is registered with the right choices --
    and the *second* part -- ``SecretsManager.from_config`` honours
    the env var set by the dispatch body.
    """
    from job_bot.container import AppContainer

    monkeypatch.delenv("HH_SECRETS_BACKEND", raising=False)
    # Re-import to reset any cached test state.
    from job_bot.cli import BUILTIN_OPERATIONS

    parser = AppContainer._build_parser(BUILTIN_OPERATIONS)

    # Default: flag absent -- ``secrets_backend`` defaults to ``None``
    # so the rest of the code keeps reading from the on-disk config
    # / env. We have to give argparse a valid sub-command so it does
    # not print help and ``SystemExit``.
    args = parser.parse_args(["config"])
    assert getattr(args, "secrets_backend", None) is None

    # Set the flag and confirm argparse stores the right value.
    args = parser.parse_args(["--secrets-backend", "keyring", "config"])
    assert args.secrets_backend == "keyring"

    # Invalid value is rejected by ``choices=`` -- argparse exits with
    # a SystemExit on a bad choice; we just want the surface
    # registered, so the negative path is implicit.

    # The manager honours the env var set by the flag.
    monkeypatch.setenv("HH_SECRETS_BACKEND", "keyring")
    # Inject a fake keyring module so ``KeyringBackend`` constructs
    # without an ``ImportError`` on this test host.
    monkeypatch.setitem(sys.modules, "keyring", _FakeKeyringModule())
    mgr = SecretsManager.from_config({})
    assert isinstance(mgr._backend, KeyringBackend)  # noqa: SLF001


def test_legacy_cli_parser_also_exposes_flag() -> None:
    """The legacy ``HHApplicantTool._create_parser`` must also expose
    the ``--secrets-backend`` flag.

    The ``python -m job_bot`` / ``hh-applicant-tool`` entry point
    goes through the legacy stub until issue #155 swaps it for
    :class:`AppContainer`; both parsers must accept the flag so a
    user can pass ``--secrets-backend keyring`` regardless of which
    path the dispatch takes.
    """
    from job_bot._legacy_compat.main_stub import HHApplicantTool

    parser = HHApplicantTool._create_parser()
    args = parser.parse_args(["--secrets-backend", "vault", "config"])
    assert args.secrets_backend == "vault"
