"""Facade that picks a :class:`SecretsBackend` and forwards to it (issue #206).

The :class:`SecretsManager` is the only object the rest of the
codebase is meant to import. It owns a single :class:`SecretsBackend`
instance, picks it from a config dict (or accepts it directly), and
forwards ``get`` / ``set`` to it. Wrapping the backend behind a
manager buys us three things:

1. **Default value handling.** :meth:`get` accepts a ``default=...``
   kwarg so callers do not have to write ``value if (value := b.get(k)) is not None else fallback`` -- they write ``m.get(k, default=fallback)``.
2. **Config-driven wiring.** :meth:`from_config` is the one place that
   knows about the ``"env"`` / ``"keyring"`` / ``"vault"`` string
   names; the rest of the code never spells them.
3. **A single seam to inject a fake.** Tests pass a plain
   :class:`SecretsBackend`-shaped object (a dict-like in-memory
   store) into the manager and the manager does not notice.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Final

from job_bot.shared.secrets.backend import SecretsBackend
from job_bot.shared.secrets.env_backend import EnvBackend
from job_bot.shared.secrets.errors import SecretNotFoundError
from job_bot.shared.secrets.keyring_backend import (
    DEFAULT_SERVICE_NAME,
    KeyringBackend,
)
from job_bot.shared.secrets.vault_backend import VaultBackend

#: The env-var name consulted by :meth:`SecretsManager.from_config` as
#: an override for ``config["secrets"]["backend"]``. The precedence
#: is ``HH_SECRETS_BACKEND`` (CLI / 12-factor) > ``config["secrets"]["backend"]``
#: (file) > ``"env"`` (default). Surfacing the constant keeps the
#: CLI wiring honest.
HH_SECRETS_BACKEND_ENV: Final[str] = "HH_SECRETS_BACKEND"

#: Names of the backends the factory knows how to build. Kept in one
#: place so the ``ValueError`` message and the factory dispatch do
#: not drift apart.
_KNOWN_BACKENDS: Final[frozenset[str]] = frozenset(
    {EnvBackend.NAME, KeyringBackend.NAME, VaultBackend.NAME}
)


class SecretsManager:
    """A thin facade over a :class:`SecretsBackend`.

    Args:
        backend: The backend to dispatch to. Must satisfy
            :class:`SecretsBackend`. A fresh :class:`EnvBackend` is
            used when ``None`` is passed, which keeps the manager
            safe to default-construct in tests and in places that
            have not yet been wired with a real backend.
    """

    def __init__(self, backend: SecretsBackend | None = None) -> None:
        self._backend: SecretsBackend = backend or EnvBackend()
        # ``__init__`` of a keyring or vault backend is allowed to
        # raise (``ImportError`` / ``NotImplementedError``); we
        # surface those directly so the caller can fix the env, not
        # silently fall back to :class:`EnvBackend`.
        self._lock = threading.Lock()

    # ─── public API ────────────────────────────────────────────────

    def get(
        self,
        name: str,
        *,
        default: str | None = None,
    ) -> str | None:
        """Return the secret named ``name`` (or ``default`` if absent).

        ``default`` is also returned when the backend raises
        :class:`SecretNotFoundError` (a "missing" answer that the
        manager is allowed to soften). Other exceptions propagate so
        the caller can fail fast on a broken backend.
        """
        with self._lock:
            try:
                value = self._backend.get(name)
            except SecretNotFoundError:
                return default
        if value is None:
            return default
        return value

    def set(self, name: str, value: str) -> None:
        """Forward to :meth:`SecretsBackend.set` under a lock.

        The lock is a coarse per-manager serialiser. Backends with
        their own thread-safety (``EnvBackend`` delegates to
        ``os.environ``, ``KeyringBackend`` delegates to ``keyring``)
        could in principle drop it, but having a single point of
        synchronisation keeps the future :class:`VaultBackend` impl
        free to cache / batch writes without callers having to know.
        """
        with self._lock:
            self._backend.set(name, value)

    @property
    def backend(self) -> SecretsBackend:
        """Return the underlying backend (read-only)."""
        return self._backend

    def __repr__(self) -> str:
        return f"SecretsManager(backend={self._backend!r})"

    # ─── factory ───────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any] | None,
        *,
        env: dict[str, str] | None = None,
    ) -> SecretsManager:
        """Build a manager from the standard config dict.

        Precedence for choosing the backend (highest to lowest):

        1. ``env[HH_SECRETS_BACKEND]`` -- pass ``env=os.environ`` for
           the production path; tests pass a dict for isolation.
        2. ``config["secrets"]["backend"]`` -- the on-disk config.
        3. :class:`EnvBackend` (the default) -- preserves the
           pre-issue-#206 behaviour for users who never set anything.

        Args:
            config: The application config dict. May be ``None`` or
                empty; both fall through to the default backend.
            env: The environment mapping to consult for
                ``HH_SECRETS_BACKEND``. Defaults to :data:`os.environ`
                for production; tests can pass an isolated dict.

        Returns:
            A :class:`SecretsManager` wrapping the chosen backend.

        Raises:
            ValueError: if the configured backend name is not one of
                ``env`` / ``keyring`` / ``vault``.
        """
        env_map = env if env is not None else os.environ
        secrets_section: dict[str, Any] = {}
        if config:
            raw = config.get("secrets")
            if isinstance(raw, dict):
                secrets_section = raw

        backend_name: str | None = env_map.get(HH_SECRETS_BACKEND_ENV)
        if backend_name is None:
            raw_name = secrets_section.get("backend")
            if isinstance(raw_name, str) and raw_name:
                backend_name = raw_name
        if backend_name is None:
            backend_name = EnvBackend.NAME

        service_name_raw = secrets_section.get("service_name")
        service_name = (
            service_name_raw
            if isinstance(service_name_raw, str) and service_name_raw
            else DEFAULT_SERVICE_NAME
        )

        backend = cls._build_backend(backend_name, service_name=service_name)
        return cls(backend=backend)

    # ─── private helpers ───────────────────────────────────────────

    @staticmethod
    def _build_backend(
        name: str,
        *,
        service_name: str,
    ) -> SecretsBackend:
        """Instantiate a backend by short name.

        Kept as a class method (not a module-level function) so the
        import graph stays small -- a test that only uses
        :class:`EnvBackend` does not need :class:`KeyringBackend` or
        :class:`VaultBackend` to be importable. (For the keyring
        path this is moot: :class:`KeyringBackend` itself lazy-loads
        ``keyring``.)
        """
        if name == EnvBackend.NAME:
            return EnvBackend()
        if name == KeyringBackend.NAME:
            return KeyringBackend(service_name=service_name)
        if name == VaultBackend.NAME:
            return VaultBackend()
        # ``sorted(...)`` makes the error message deterministic so a
        # test asserting on it is not flaky.
        valid = ", ".join(sorted(_KNOWN_BACKENDS))
        raise ValueError(
            f"Unknown secrets backend: {name!r}. Valid options: {valid}."
        )


__all__ = ["HH_SECRETS_BACKEND_ENV", "SecretsManager"]
