"""Read-only :class:`SecretsBackend` backed by ``os.environ`` (issue #206).

The :class:`EnvBackend` is the default backend and preserves the
exact behaviour the rest of the codebase has relied on so far --
``os.getenv(name)`` for reads, with no write path. Calling ``set`` on
an :class:`EnvBackend` is a programming error: writes would only
live in the current process's env and the next restart would silently
lose them, so the backend fails fast instead.
"""

from __future__ import annotations

import os
from typing import Final

from job_bot.shared.secrets.errors import SecretBackendUnavailableError


class EnvBackend:
    """Read secrets from :data:`os.environ`.

    Thread-safety: :func:`os.getenv` is thread-safe in CPython; the
    backend holds no state of its own. The default backend.
    """

    #: Human-readable name used by :meth:`__repr__` and by the
    #: :class:`SecretsManager` when surfacing a backend choice in
    #: a log line.
    NAME: Final[str] = "env"

    def get(self, name: str) -> str | None:
        """Return the env var ``name`` or ``None`` if unset.

        Wraps :func:`os.getenv` so the contract is identical to what
        the rest of the code used to do by hand.
        """
        return os.getenv(name)

    def set(self, name: str, value: str) -> None:
        """Always raise -- the env is a read-only source for us.

        Raises:
            SecretBackendUnavailableError: always.
        """
        raise SecretBackendUnavailableError(
            "EnvBackend is read-only: writing to os.environ would "
            "silently lose the value on the next process restart. "
            "Use KeyringBackend for persistence."
        )

    def __repr__(self) -> str:
        return "EnvBackend()"


__all__ = ["EnvBackend"]
