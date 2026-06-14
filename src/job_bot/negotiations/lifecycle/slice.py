"""Main entry point for the ``negotiations.lifecycle`` VSA sub-slice (issue #137).

The slice replaces the legacy ``clear_negotiations`` operation. It
encapsulates the four lifecycle actions:

* decline a refused / discarded / old negotiation,
* optionally delete the chat (``--delete-chat``),
* optionally blacklist the employer (``--blacklist-discard`` or
  ``--block-ats`` for fast-response ``ATS`` signals).

Typical wiring (production)::

    from job_bot.negotiations.lifecycle import (
        create_negotiation_lifecycle_slice,
    )
    from job_bot.shared.adapters.hh_api import HHApiAdapter

    api = HHApiAdapter(tool=tool)
    slice_ = create_negotiation_lifecycle_slice(
        api=api,
        blacklisted_employers=set(tool.get_blacklisted()),
    )
    result = slice_.run(blacklist_discard=True, delete_chat=True)
    print(result.as_dict())
"""

from __future__ import annotations

import logging
from typing import Any

from job_bot.negotiations.lifecycle.handlers.state_machine import (
    LifecycleStateMachine,
)
from job_bot.negotiations.lifecycle.models.negotiation_state import (
    NegotiationLifecycleResult,
)
from job_bot.negotiations.lifecycle.ports.api_port import LifecycleApiPort

logger = logging.getLogger(__name__)


class NegotiationLifecycleSlice:
    """The ``negotiations.lifecycle`` sub-slice (VSA, issue #137).

    Public surface:

    * :meth:`run` — the high-level orchestrator.
    * :attr:`state_machine` — the underlying handler (for tests).
    * :attr:`api` — the API port (read-only).
    """

    def __init__(
        self,
        *,
        api: Any,
        blacklisted_employers: set[str] | None = None,
        state_machine: LifecycleStateMachine | None = None,
    ) -> None:
        # The ``api`` argument must satisfy :class:`LifecycleApiPort`.
        # In production the legacy ``HHApiClient``/``MegaTool`` is
        # wrapped by an adapter; in tests a hand-rolled fake is used.
        self._api = api
        self._blacklisted = set(blacklisted_employers or ())
        self._state_machine = state_machine or LifecycleStateMachine(
            api=api,  # type: ignore[arg-type]
            blacklisted_employers=self._blacklisted,
        )

    # ─── Read-only accessors ─────────────────────────────────────

    @property
    def api(self) -> Any:
        """The API port passed in (read-only)."""
        return self._api

    @property
    def state_machine(self) -> LifecycleStateMachine:
        return self._state_machine

    # ─── Public API ──────────────────────────────────────────────

    def run(
        self,
        *,
        older_than: int | None = None,
        blacklist_discard: bool = False,
        delete_chat: bool = False,
        block_ats: bool = False,
        dry_run: bool = False,
    ) -> NegotiationLifecycleResult:
        """Walk every negotiation and apply the lifecycle actions.

        Args:
            older_than: if set, decline any negotiation whose
                ``updated_at`` is older than this many days.  When
                set, the default ``refusal``/``discard`` filter is
                bypassed (matches the legacy ``--older-than``
                behaviour, which doubles as an "include active
                chats" mode).
            blacklist_discard: blacklist the employer of every
                declined negotiation.
            delete_chat: also delete the chat via the
                ``/applicant/negotiations/trash`` web endpoint.
            block_ats: blacklist the employer when the response
                time is suspiciously fast (≤ 16 minutes).
            dry_run: count what *would* happen without calling any
                mutating endpoint.

        Returns:
            A :class:`NegotiationLifecycleResult` with per-action
            counters.
        """
        result = self._state_machine.run(
            older_than=older_than,
            blacklist_discard=blacklist_discard,
            delete_chat=delete_chat,
            block_ats=block_ats,
            dry_run=dry_run,
        )
        logger.info("Lifecycle run finished: %s", result.as_dict())
        return result


def create_negotiation_lifecycle_slice(
    *,
    api: LifecycleApiPort,
    blacklisted_employers: set[str] | None = None,
) -> NegotiationLifecycleSlice:
    """Factory for :class:`NegotiationLifecycleSlice`."""
    return NegotiationLifecycleSlice(
        api=api,
        blacklisted_employers=blacklisted_employers,
    )


__all__ = [
    "NegotiationLifecycleSlice",
    "create_negotiation_lifecycle_slice",
    "NegotiationLifecycleResult",
    "LifecycleApiPort",
]
