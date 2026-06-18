"""Errors raised by the secrets-backends module (issue #206).

Two distinct exception types so callers can branch on the failure
mode:

* :class:`SecretNotFoundError` -- the backend answered but the secret
  is not present. The caller can substitute a default.
* :class:`SecretBackendUnavailableError` -- the backend itself is
  unusable: missing optional dep, read-only source, network down for
  Vault, etc. The caller should fail fast.
"""

from __future__ import annotations


class SecretNotFoundError(LookupError):
    """The backend answered, but the requested secret is not set.

    Inherits from :class:`LookupError` so generic ``except LookupError``
    handlers (the Python convention for missing-key) keep working
    unchanged.
    """


class SecretBackendUnavailableError(RuntimeError):
    """The backend cannot service the request at all.

    Examples:

    * :class:`EnvBackend.set` -- the env is read-only.
    * :class:`KeyringBackend` constructed without the ``keyring`` extra
      installed.
    * :class:`VaultBackend.get` / ``.set`` -- the Vault impl is a
      placeholder in this PR.
    """


__all__ = ["SecretBackendUnavailableError", "SecretNotFoundError"]
