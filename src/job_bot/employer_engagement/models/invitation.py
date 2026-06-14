"""Invitation / message DTOs for the ``employer_engagement`` slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MessageRecord:
    """A single message in a negotiation chat.

    Attributes:
        id: HH message id.
        text: Plain-text content (already HTML-stripped by the API).
        author_type: ``"employer"`` for incoming, ``"applicant"`` for
            outgoing.
        created_at: ISO-8601 datetime as returned by HH.
    """

    id: str
    text: str
    author_type: str
    created_at: str

    @property
    def is_from_employer(self) -> bool:
        return self.author_type == "employer"


@dataclass
class Invitation:
    """A normalized negotiation record used by the slice.

    The legacy code worked with ``api.datatypes.Negotiation`` TypedDicts;
    the VSA layer normalizes the subset of fields the engagement logic
    actually needs, so the handlers don't reach into a TypedDict shape
    the test suite doesn't have to mock in full.
    """

    id: str
    state_id: str
    updated_at: str
    resume_id: str
    vacancy_name: str
    employer_id: str
    employer_name: str
    employer_alternate_url: str
    vacancy_alternate_url: str
    viewed_by_opponent: bool = True
    salary_from: int | None = None
    salary_to: int | None = None
    salary_currency: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_invitation(self) -> bool:
        """``True`` for ``invitation*`` states (HH uses ``invitation`` and
        ``invitation_***`` family ids)."""
        return self.state_id.startswith("inv")

    @property
    def is_discarded(self) -> bool:
        return self.state_id == "discard"

    def placeholder_dict(
        self,
        *,
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        phone: str = "",
        resume_title: str = "",
    ) -> dict[str, str]:
        """Return placeholder substitutions for template messages.

        The legacy :func:`hh_applicant_tool.utils.string.rand_text` does
        ``%`` substitution on the user's template; we mirror that here
        so the slice is self-contained.
        """
        return {
            "vacancy_name": self.vacancy_name,
            "employer_name": self.employer_name,
            "resume_title": resume_title,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
        }
