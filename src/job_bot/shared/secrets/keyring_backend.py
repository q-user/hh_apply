"""System-keyring :class:`SecretsBackend` (issue #206).

The :class:`KeyringBackend` reads / writes secrets via the ``keyring``
PyPI package, which delegates to the platform's credential store:

* **macOS** -- Keychain.
* **Linux** -- Secret Service (``gnome-keyring``, ``kwallet``, ...).
* **Windows** -- Credential Vault.

Because ``keyring`` is an *optional* dependency (install via ``uv pip
install -e .[secrets]``), the import is lazy: the module top-level
does not touch ``keyring`` at all, so ``import
job_bot.shared.secrets`` succeeds even on machines without the
extra installed. Only the :class:`KeyringBackend` constructor pays the
import cost, and it surfaces a helpful :class:`ImportError` if the
extra is missing.

The ``service_name`` constructor argument scopes the read / write
namespace in the OS keyring so two apps (or two profiles of the same
app) sharing the same keyring do not collide. The default
``"hh-applicant-tool"`` is what the user-facing CLI uses; tests pass
their own service name so they do not pollute the user's real keyring.
"""

from __future__ import annotations

import importlib
from typing import Any, Final, cast

from job_bot.shared.secrets.errors import SecretBackendUnavailableError

#: Name of the PyPI extra that ships the ``keyring`` package.
#: Surfaced in the :class:`ImportError` message so a user seeing it
#: can recover via ``uv pip install -e .[secrets]``.
KEYRING_EXTRA_NAME: Final[str] = "secrets"

#: Default ``service_name`` argument to the OS keyring.
#: Chosen to be specific enough to not collide with other apps on
#: the same machine but short enough to be readable in the macOS
#: Keychain / ``secret-tool list`` output.
DEFAULT_SERVICE_NAME: Final[str] = "hh-applicant-tool"


class KeyringBackend:
    """Read / write secrets via the ``keyring`` PyPI package.

    Args:
        service_name: The ``service`` argument passed to
            ``keyring.get_password`` / ``keyring.set_password``. The
            same name must be used to read what was written --
            consider it a per-app namespace inside the OS keyring.

    Raises:
        ImportError: if the ``keyring`` package is not installed.
            The message names the ``[secrets]`` PyPI extra so the
            user can install it with a single ``uv pip install -e
            .[secrets]``.
    """

    NAME: Final[str] = "keyring"

    def __init__(self, service_name: str = DEFAULT_SERVICE_NAME) -> None:
        self._service_name = service_name
        # Lazy import: do NOT move this to module top level. The
        # ``keyring`` package is an optional dep; a top-level
        # ``import keyring`` would break ``import
        # job_bot.shared.secrets`` on a vanilla install.
        self._keyring: Any = self._import_keyring()

    # ─── public API ────────────────────────────────────────────────

    def get(self, name: str) -> str | None:
        """Return the secret ``name`` or ``None`` if absent.

        The ``keyring`` package returns ``None`` for an unknown key
        (not an empty string), so we forward the result unchanged.
        The ``cast`` silences mypy's ``[no-any-return]`` since the
        underlying ``keyring`` module is untyped.
        """
        result = self._keyring.get_password(self._service_name, name)
        return cast("str | None", result)

    def set(self, name: str, value: str) -> None:
        """Persist ``value`` under ``name`` in the OS keyring.

        ``keyring.set_password`` raises ``keyring.errors.PasswordSetError``
        on some platforms when the password manager is locked or
        unavailable; we re-raise that as a
        :class:`SecretBackendUnavailableError` so callers only need
        to know about our exception types.

        Raises:
            SecretBackendUnavailableError: if the platform keyring
                refuses the write.
        """
        try:
            self._keyring.set_password(self._service_name, name, value)
        except Exception as exc:  # noqa: BLE001 -- boundary; rewrap
            raise SecretBackendUnavailableError(
                f"KeyringBackend.set({name!r}) failed: {exc}"
            ) from exc

    @property
    def service_name(self) -> str:
        """Return the OS keyring ``service`` namespace used by this backend."""
        return self._service_name

    def __repr__(self) -> str:
        return f"KeyringBackend(service_name={self._service_name!r})"

    # ─── private helpers ────────────────────────────────────────────

    @staticmethod
    def _import_keyring() -> Any:
        """Lazy-load the ``keyring`` package with a helpful error message.

        The message points at the ``[secrets]`` PyPI extra so a user
        who stumbles into the ``ImportError`` can recover in one
        command. A plain ``ModuleNotFoundError`` would just say
        "No module named 'keyring'", which is far less actionable.
        """
        try:
            return importlib.import_module("keyring")
        except ImportError as exc:
            raise ImportError(
                "The 'keyring' package is required for KeyringBackend. "
                "Install it via the 'secrets' optional extra: "
                f"`uv pip install -e .[{KEYRING_EXTRA_NAME}]` "
                "(or `pip install '.[secrets]')."
            ) from exc


__all__ = ["DEFAULT_SERVICE_NAME", "KEYRING_EXTRA_NAME", "KeyringBackend"]
