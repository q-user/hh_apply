"""Port for reading / writing named secrets (issue #206).

A :class:`SecretsBackend` is the only seam the :class:`SecretsManager`
facade talks to. Three concrete backends ship in the box (see
:mod:`job_bot.shared.secrets.env_backend`, :mod:`.keyring_backend`, and
:mod:`.vault_backend`); a future ``VaultBackend`` implementation can
drop in without touching the manager or any call site.

Design notes:

* **Protocol, not ABC.** The shared kernel uses structural typing
  throughout (``health.HealthChecks`` is another example) so callers
  can pass plain objects that satisfy the shape. The
  ``@runtime_checkable`` decorator lets ``isinstance(backend,
  SecretsBackend)`` work in tests.
* **Both ``get`` and ``set``.** Some backends are read-only (the env,
  for example); they raise :class:`SecretBackendUnavailableError` on
  ``set``. The manager does not enforce read-only-ness; the backend
  decides.
* **Strings only.** A secret is a piece of text the caller hands to an
  HTTP client / SDK. Binary secrets would need a separate type; the
  existing use cases are all strings, so we keep the surface small.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsBackend(Protocol):
    """Read / write named secrets.

    Implementations should be *thread-safe*: the long-running CLI
    daemons (``apply-worker``, ``telegram-bot``, ``max-bot``) construct
    one backend and reuse it across worker threads. The simplest way
    to satisfy this is to delegate to a thread-safe primitive (env
    reads, ``keyring``'s get/set, etc.) without holding internal state.
    """

    def get(self, name: str) -> str | None:
        """Return the secret named ``name`` or ``None`` if absent.

        ``None`` (not ``""``) signals absence; callers that want a
        different default can pass it to :meth:`SecretsManager.get`.
        A backend that wants to surface "missing" as an exception
        should raise :class:`SecretNotFoundError` -- the manager
        will translate that into the right return value.
        """
        ...

    def set(self, name: str, value: str) -> None:
        """Persist ``value`` under ``name``.

        Backends that cannot persist (read-only env, missing
        optional dep, unimplemented placeholder) must raise
        :class:`SecretBackendUnavailableError` rather than silently
        no-op. Silent no-op masks bugs.
        """
        ...


__all__ = ["SecretsBackend"]
