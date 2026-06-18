"""Externalised secrets backends (issue #206).

The shared kernel used to read every secret-shaped value straight
from ``os.environ`` via ad-hoc ``os.getenv`` / ``os.environ.get``
calls scattered across the codebase. That worked, but it locked the
deployment story to a single source of truth: env vars, full stop.

This package introduces a tiny, dependency-light port
(:class:`SecretsBackend`) with three concrete implementations:

* :class:`EnvBackend` -- the current behaviour, default.
* :class:`KeyringBackend` -- delegates to the OS keyring via the
  optional ``keyring`` PyPI package (install via ``uv pip install -e
  .[secrets]``).
* :class:`VaultBackend` -- a placeholder for a future HashiCorp
  Vault integration; the interface allows a drop-in impl.

A :class:`SecretsManager` facade picks the backend from
``config["secrets"]["backend"]`` (or the ``HH_SECRETS_BACKEND`` env
var) and is the only object the rest of the codebase is expected to
import.

Typical usage::

    from job_bot.shared.secrets import SecretsManager

    manager = SecretsManager.from_config(config_dict)
    token = manager.get("HH_OAUTH_TOKEN")
    if token is None:
        raise SystemExit("HH_OAUTH_TOKEN is required")
"""

from __future__ import annotations

from job_bot.shared.secrets.backend import SecretsBackend
from job_bot.shared.secrets.env_backend import EnvBackend
from job_bot.shared.secrets.errors import (
    SecretBackendUnavailableError,
    SecretNotFoundError,
)
from job_bot.shared.secrets.keyring_backend import (
    DEFAULT_SERVICE_NAME,
    KEYRING_EXTRA_NAME,
    KeyringBackend,
)
from job_bot.shared.secrets.manager import (
    HH_SECRETS_BACKEND_ENV,
    SecretsManager,
)
from job_bot.shared.secrets.vault_backend import VaultBackend

__all__ = [
    "DEFAULT_SERVICE_NAME",
    "EnvBackend",
    "HH_SECRETS_BACKEND_ENV",
    "KEYRING_EXTRA_NAME",
    "KeyringBackend",
    "SecretBackendUnavailableError",
    "SecretNotFoundError",
    "SecretsBackend",
    "SecretsManager",
    "VaultBackend",
]
