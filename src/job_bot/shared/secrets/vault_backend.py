"""Placeholder :class:`SecretsBackend` for HashiCorp Vault (issue #206).

The :class:`VaultBackend` exists so the :class:`SecretsManager` facade
has a complete type table from day one. The actual Vault integration
(``hvac``-based client, token auth, KV v2 path conventions, lease
renewal, etc.) is out of scope for this PR -- see the linked issue.

The placeholder raises :class:`NotImplementedError` on every call
rather than returning ``None``: a missing implementation must not be
confusable with a missing secret. A caller that does
``manager.get("OAUTH_TOKEN")`` and gets ``None`` cannot tell whether
the key was absent or whether the Vault backend silently failed; a
hard exception is the honest answer.
"""

from __future__ import annotations

from typing import Final


class VaultBackend:
    """Placeholder for a future HashiCorp Vault backend.

    The class is *intentionally* minimal: it has no constructor
    arguments, no client state, and no I/O. A future implementation
    will accept ``url`` / ``token`` / ``mount_point`` kwargs and
    delegate to the ``hvac`` PyPI package. Adding those now would be
    guessing at the final API.
    """

    NAME: Final[str] = "vault"

    def get(self, name: str) -> str | None:
        """Raise :class:`NotImplementedError`.

        The :class:`SecretsManager` does not catch this -- the user
        must opt out of the placeholder by setting
        ``config["secrets"]["backend"] = "env"`` (or ``"keyring"``)
        once the real Vault impl lands.
        """
        raise self._unimplemented("get", name)

    def set(self, name: str, value: str) -> None:
        """Raise :class:`NotImplementedError` (symmetric with :meth:`get`)."""
        raise self._unimplemented("set", name)

    @staticmethod
    def _unimplemented(op: str, name: str) -> NotImplementedError:
        return NotImplementedError(
            f"VaultBackend.{op}({name!r}) is not implemented yet. "
            "Vault support is tracked in the issue linked to PR #206. "
            "Use EnvBackend (default) or KeyringBackend in the meantime."
        )

    def __repr__(self) -> str:
        return "VaultBackend(<placeholder>)"


__all__ = ["VaultBackend"]
