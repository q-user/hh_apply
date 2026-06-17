"""DailyDigestPort -- Protocol contract for the daily digest service.

The slice's :class:`DigestHandler` depends on this Protocol; the concrete
:class:`hh_applicant_tool.services.daily_digest.DailyDigestService` is
provided by the slice (via the default factory) or by tests.
"""

from __future__ import annotations

from typing import Any, Protocol


class DailyDigestPort(Protocol):
    """Interface used by the slice's digest handler.

    Mirrors the public surface of
    :class:`hh_applicant_tool.services.daily_digest.DailyDigestService`.
    """

    def send(self, force: bool = False) -> Any:
        """Build and (maybe) send the daily digest.

        Returns a ``DigestResult``-like object with ``sent``,
        ``skipped_reason``, ``total_drafts`` and ``message`` attributes.
        """
        ...

    def collect_groups(self) -> list[Any]:
        """Return the list of :class:`DraftGroup` for stats / preview."""
        ...
