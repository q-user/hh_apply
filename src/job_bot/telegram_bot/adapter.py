"""``TelegramBotAdapter`` -- operation-facing adapter for the VSA slice.

The CLI ``telegram-bot`` operation only needs a thin surface on top of
:class:`TelegramBotSlice`:

* ``transport``         -- the underlying :class:`TelegramTransport`
                            (for the polling loop in ``Operation.run``);
* ``dispatch_update``   -- delegate to :meth:`BotService.dispatch_update`;
* ``send_digest``       -- delegate to :meth:`DailyDigestService.send`;
* ``close``             -- release the slice's long-lived DB connection;
* ``bot_service``       -- the slice's :class:`BotService` (for tests);
* ``slice``             -- the underlying slice (for tests / advanced
                            callers that want to inspect the digest /
                            review / command handlers directly).

Putting the adapter inside the slice package keeps the dependency
arrow pointing the right way (CLI / container depend on the slice,
not the other way around) and matches the pattern used for
:mod:`job_bot.max_bot.requests_transport`.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _SliceLike(Protocol):
    """Minimal structural contract the adapter relies on.

    Using a Protocol (not the concrete :class:`TelegramBotSlice`) keeps
    the adapter testable and lets the production wiring be lazy.
    """

    @property
    def transport(self) -> Any: ...

    @property
    def service(self) -> Any: ...

    @property
    def digest(self) -> Any: ...

    def close(self) -> None: ...


class TelegramBotAdapter:
    """Thin operation-facing wrapper around a VSA :class:`TelegramBotSlice`.

    Args:
        slice_: a :class:`TelegramBotSlice` (or any object satisfying
            :class:`_SliceLike`).
    """

    def __init__(self, slice_: _SliceLike) -> None:
        self._slice = slice_

    # ─── Operation surface ───────────────────────────────────────

    @property
    def transport(self) -> Any:
        return self._slice.transport

    @property
    def slice(self) -> _SliceLike:
        """Underlying slice (for tests / advanced callers)."""
        return self._slice

    @property
    def bot_service(self) -> Any:
        """Underlying :class:`BotService` (for tests)."""
        return self._slice.service

    def dispatch_update(self, update: dict[str, Any]) -> Any:
        """Forward ``update`` to the slice's ``BotService.dispatch_update``.

        Returns whatever the service returns (the outgoing message DTO
        or ``None`` for non-command updates).
        """
        return self._slice.service.dispatch_update(update)

    def send_digest(self, *, force: bool = False) -> Any:
        """Forward ``send(force=...)`` to the slice's digest service.

        Returns the ``DigestResult`` (or whatever the service returns).
        """
        return self._slice.digest.send(force=force)

    def close(self) -> None:
        """Release the slice's long-lived DB connection (if any)."""
        try:
            self._slice.close()
        except Exception:  # noqa: BLE001
            logger.exception("TelegramBotAdapter.close failed")


def create_telegram_bot_adapter(slice_: _SliceLike) -> TelegramBotAdapter:
    """Factory for :class:`TelegramBotAdapter`.

    Mirrors the ``create_*_slice`` factories in the slice package so
    callers can build the adapter from a single entry point.
    """
    return TelegramBotAdapter(slice_=slice_)
