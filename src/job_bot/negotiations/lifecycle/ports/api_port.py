"""Protocol interfaces for the ``negotiations.lifecycle`` sub-slice.

The slice depends on a single port that maps onto the legacy
``api_client`` / ``session.post(...)`` surface used by
:mod:`hh_applicant_tool.operations.clear_negotiations`.

In production the legacy ``HHApiClient`` + ``MegaTool.session`` is
wrapped by an adapter; in tests a hand-rolled fake is used.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LifecycleApiPort(Protocol):
    """HH API surface used by the lifecycle sub-slice.

    Mirrors the legacy ``HHApplicantTool`` / ``api_client`` methods:

    * ``iter_negotiations(status='all')`` — generator over
      ``/negotiations`` paginated.
    * ``decline_negotiation(id, with_decline_message=...)`` —
      ``DELETE /negotiations/active/{id}`` (the legacy code used
      ``with_decline_message`` as a flag for whether to send a
      "decline" reason to the employer).
    * ``blacklist_employer(id)`` — ``PUT /employers/blacklisted/{id}``.
    * ``delete_chat(topic)`` — web-trash POST that hides the chat
      from the dashboard; the legacy operation called
      ``https://hh.ru/applicant/negotiations/trash`` directly with
      an XSRF-protected payload.
    """

    def iter_negotiations(self, status: str = "all") -> Iterable[Any]: ...

    def decline_negotiation(
        self,
        negotiation_id: str,
        *,
        with_decline_message: bool,
    ) -> None: ...

    def blacklist_employer(self, employer_id: str) -> None: ...

    def delete_chat(self, topic: int | str) -> bool: ...
