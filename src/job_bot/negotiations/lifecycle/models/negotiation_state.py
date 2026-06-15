"""DTOs for the ``negotiations.lifecycle`` sub-slice.

Defines:

* :class:`NegotiationRecord` — a normalized negotiation record
  (mirrors the subset of fields the legacy operation consumed).
* :class:`NegotiationLifecycleResult` — a counter object returned by
  :meth:`NegotiationLifecycleSlice.run`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NegotiationRecord:
    """A normalized negotiation record consumed by the lifecycle handler.

    The legacy operation worked with ``api.datatypes.Negotiation``
    TypedDicts; the VSA layer normalizes the subset of fields the
    lifecycle logic actually needs, so the handler doesn't have to
    reach into a TypedDict shape tests would otherwise have to
    fully mock.
    """

    id: str
    state_id: str
    created_at: str
    updated_at: str
    employer_id: str | None
    employer_name: str = ""
    employer_alternate_url: str = ""
    vacancy_name: str = ""
    vacancy_alternate_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_refused(self) -> bool:
        """``True`` for ``refusal`` and ``discard`` states — both
        represent a closed, undesirable chat."""
        return self.state_id in {"refusal", "discard"}

    @property
    def has_employer(self) -> bool:
        return bool(self.employer_id)

    def response_seconds(self) -> float | None:
        """Return the seconds between ``created_at`` and ``updated_at``.

        ``None`` if either date can't be parsed.
        """
        try:
            from job_bot.shared.utils.datetime_utils import (
                try_parse_datetime,
            )

            c = try_parse_datetime(self.created_at)
            u = try_parse_datetime(self.updated_at)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(c, dt.datetime) or not isinstance(u, dt.datetime):
            return None
        # Normalize naive datetimes to UTC for the subtraction.
        if c.tzinfo is None:
            c = c.replace(tzinfo=dt.timezone.utc)
        if u.tzinfo is None:
            u = u.replace(tzinfo=dt.timezone.utc)
        return (u - c).total_seconds()


@dataclass
class NegotiationLifecycleResult:
    """Counters returned by :meth:`NegotiationLifecycleSlice.run`.

    All fields are populated even in ``dry_run`` mode (the slice
    counts what *would* happen) so callers can render a summary
    regardless of mode.
    """

    declined: int = 0
    blacklisted: int = 0
    chats_deleted: int = 0
    ats_detected: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "declined": self.declined,
            "blacklisted": self.blacklisted,
            "chats_deleted": self.chats_deleted,
            "ats_detected": self.ats_detected,
            "failed": self.failed,
        }
