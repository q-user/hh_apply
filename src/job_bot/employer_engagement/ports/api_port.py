"""Protocol interfaces for the ``employer_engagement`` slice.

The slice depends on four small Protocols that map onto the legacy
``api_client`` / ``chat_ai`` / ``tool.get_negotiations()`` /
``tool.get_messages()`` surfaces. Tests use fakes that satisfy these
ports; production wiring wraps the legacy ``HHApiClient`` /
``MegaTool`` objects in adapters.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from job_bot.employer_engagement.models.invitation import (
    Invitation,
    MessageRecord,
)


@runtime_checkable
class NegotiationSourcePort(Protocol):
    """Returns the active negotiations to process.

    Mirrors the legacy ``HHApplicantTool.get_negotiations(status='active')``
    generator. The slice only ever passes ``status='active'``.
    """

    def iter_negotiations(
        self, status: str = "active"
    ) -> Iterable[Invitation | dict[str, Any]]: ...


@runtime_checkable
class MessageSourcePort(Protocol):
    """Returns the message history of a single negotiation chat."""

    def iter_messages(
        self, negotiation_id: str
    ) -> Iterable[MessageRecord | dict[str, Any]]: ...


@runtime_checkable
class EmployerActionsPort(Protocol):
    """Write-side surface: post messages and blacklist employers.

    The legacy code used ``api_client.post('/negotiations/{id}/messages', message=..., delay=...)``
    and ``api_client.put('/employers/blacklisted/{id}')``; this port
    collapses both into explicit method calls so the slice doesn't
    hard-code HH API paths.
    """

    def post_message(
        self,
        negotiation_id: str,
        *,
        text: str,
        delay: float | None = None,
    ) -> None: ...

    def blacklist_employer(self, employer_id: str) -> None: ...


@runtime_checkable
class AIClientPort(Protocol):
    """Generates a reply body given a prompt."""

    def complete(self, query: str) -> str: ...
