"""CLI-операция ``whoami`` (VSA-rewrite issue #147).

Thin VSA adapter over :class:`ConfigAuthSlice.users` and
:attr:`StorageFacade.settings`. The op reads the current user via the
slice's :class:`UserPort`, stores ``full_name``/``email``/``phone`` in
the settings table, and prints a single line with the user id, name,
and counters.

The slice is constructor-injected — no more ``tool: HHApplicantTool``
argument. Wire the slice in :class:`AppContainer` (or the new
``job_bot.cli`` composition root) and pass it to ``Operation(slice_=...)``.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _UserSlice(Protocol):
    """Minimal slice contract the op depends on (for duck typing)."""

    @property
    def users(self) -> Any: ...
    @property
    def settings(self) -> Any: ...
    @property
    def api_client(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``whoami`` (legacy-совместимый shell)."""


def fmt_plus(n: int) -> str:
    """Format ``n`` as ``+N`` (or ``0``)."""
    assert n >= 0
    return f"+{n}" if n else "0"


class Operation(BaseOperation):
    """Выведет текущего пользователя."""

    # Это алиасы команды
    __aliases__ = ("id",)

    def __init__(self, slice_: _UserSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        # ``whoami`` takes no extra arguments.
        pass

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error(
                "whoami requires a ConfigAuthSlice with users + settings ports"
            )
            return 1

        # Always go via the API client: ``/me`` is the canonical source of
        # the user id. The ``UserPort`` then enriches the payload from the
        # local DB.
        api_client = getattr(slice_, "api_client", None)
        if api_client is None:
            logger.error("whoami: slice has no api_client")
            return 1

        result: dict[str, Any] = api_client.get("me")
        user_id = str(result.get("id", ""))
        if not user_id:
            logger.error("whoami: /me returned no user id")
            return 1

        user_port = getattr(slice_, "users", None)
        user: dict[str, Any] = result
        if user_port is not None:
            try:
                fetched = user_port.get_user(user_id)
            except Exception:  # noqa: BLE001 — best-effort enrichment
                fetched = None
            if fetched:
                user = fetched

        if user.get("auth_type") != "applicant":
            logger.warning(
                "Вы вошли не как соискатель! "
                "Попробуйте авторизоваться вручную!!!"
            )

        full_name = (
            " ".join(
                filter(
                    None,
                    [
                        user.get("last_name"),
                        user.get("first_name"),
                        user.get("middle_name"),
                    ],
                )
            )
            or "Анонимный аккаунт"
        )

        # ``user_id`` was sourced from the API client. When the slice's
        # user_port returned a richer record, that record's id field
        # takes precedence (the test fixtures use this).
        display_id = str(user.get("id") or user_id)

        settings = getattr(slice_, "settings", None)
        if settings is not None:
            try:
                with settings as s:
                    s.set_value("user.full_name", full_name)
                    s.set_value("user.email", user.get("email"))
                    s.set_value("user.phone", user.get("phone"))
            except Exception:  # noqa: BLE001 — best-effort persistence
                logger.debug("whoami: failed to persist user.*", exc_info=True)

        counters = user.get("counters") or {}
        print(
            f"🆔 {display_id} {full_name} "
            f"[ 📄 {counters.get('resumes_count', 0)} "
            f"| 👁️ {fmt_plus(int(counters.get('new_resume_views', 0)))} "
            f"| ✉️ {fmt_plus(int(counters.get('unread_negotiations', 0)))} ]"
        )
        return 0


__all__ = ("Operation", "Namespace")
