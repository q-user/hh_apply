"""Fetches and filters invitations (negotiations) for the slice.

The legacy ``reply_employers`` operation iterated the
``/negotiations?status=active`` collection directly, then filtered
in-line. This handler extracts that logic so the slice can be tested
in isolation and so the filtering rules live in one place.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator
from typing import Any

from job_bot.employer_engagement.models.invitation import Invitation
from job_bot.employer_engagement.ports.api_port import (
    MessageSourcePort,
    NegotiationSourcePort,
)
from job_bot.shared.utils.datetime_utils import try_parse_datetime

logger = logging.getLogger(__name__)


class InvitationFetcher:
    """Yield invitations that should receive a reply.

    Filtering rules (mirrors the legacy code, issue #137):

    * resume must be in the published ``resumes`` set (or no resume
      filter is applied when ``resume_id`` is ``None``);
    * state must not be ``discard``;
    * if ``only_invitations`` is set, the state must start with
      ``"inv"``;
    * the employer must not be in ``blacklisted_employers``;
    * if ``period`` is set, the negotiation must have been updated
      within the last ``period`` days.
    """

    def __init__(
        self,
        *,
        source: NegotiationSourcePort,
        messages: MessageSourcePort,
        resumes: list[dict[str, Any]] | None = None,
        resume_id: str | None = None,
        only_invitations: bool = False,
        period: int | None = None,
        blacklisted_employers: set[str] | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        self._source = source
        self._messages = messages
        self._resumes = list(resumes or [])
        self._resume_id = resume_id
        self._only_invitations = only_invitations
        self._period = period
        self._blacklisted = set(blacklisted_employers or ())
        self._now = now or dt.datetime.now(dt.timezone.utc)

    @property
    def messages(self) -> MessageSourcePort:
        return self._messages

    # ─── Public API ──────────────────────────────────────────────

    def eligible(self) -> Iterator[Invitation]:
        """Yield :class:`Invitation` records that should be replied to."""
        for raw in self._source.iter_negotiations(status="active"):
            inv = self._coerce(raw)
            if inv is None:
                continue
            if not self._is_eligible(inv):
                continue
            yield inv

    # ─── Internals ───────────────────────────────────────────────

    def _is_eligible(self, inv: Invitation) -> bool:
        # Resume filter
        if not self._resume_matches(inv):
            return False
        # Discarded state is always skipped
        if inv.is_discarded:
            return False
        # only-invitations filter
        if self._only_invitations and not inv.is_invitation:
            return False
        # Period filter
        if self._period and not self._within_period(inv):
            return False
        # Blacklist filter
        if inv.employer_id and inv.employer_id in self._blacklisted:
            logger.debug(
                "Skipping blacklisted employer %s for negotiation %s",
                inv.employer_id,
                inv.id,
            )
            return False
        return True

    def _resume_matches(self, inv: Invitation) -> bool:
        """The negotiation's resume must be published and (if a specific
        ``resume_id`` was requested) match the requested id."""
        if not self._resumes:
            # No resume list available — don't filter (back-compat with
            # the legacy code which only filtered when ``tool.get_resumes()``
            # returned at least one entry).
            return True
        resume_map = {r["id"]: r for r in self._resumes}
        resume = resume_map.get(inv.resume_id)
        if resume is None:
            return False
        status = resume.get("status") or {}
        if isinstance(status, dict):
            published = status.get("id") == "published"
        else:
            # Some legacy call sites pass ``status`` as a string
            published = status == "published"
        return published

    def _within_period(self, inv: Invitation) -> bool:
        try:
            updated_at = try_parse_datetime(inv.updated_at)
        except Exception:  # noqa: BLE001
            # If the date can't be parsed, don't apply the filter
            return True
        if not isinstance(updated_at, dt.datetime):
            return True
        # Normalize: ``parse_api_datetime`` may return a naive datetime
        # if the input has no tz suffix; we work in UTC throughout.
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=dt.timezone.utc)
        days = (self._now - updated_at).days
        return days <= self._period

    @staticmethod
    def _coerce(raw: Invitation | dict[str, Any]) -> Invitation | None:
        if isinstance(raw, Invitation):
            return raw
        if not isinstance(raw, dict):
            return None
        try:
            vacancy = raw.get("vacancy") or {}
            employer = vacancy.get("employer") or {}
            resume = raw.get("resume") or {}
            state = raw.get("state") or {}
            salary = vacancy.get("salary") or {}
            return Invitation(
                id=str(raw["id"]),
                state_id=str(state.get("id", "")),
                updated_at=str(raw.get("updated_at", "")),
                resume_id=str(resume.get("id", "")),
                vacancy_name=str(vacancy.get("name", "")),
                employer_id=str(employer.get("id", "")),
                employer_name=str(employer.get("name", "")),
                employer_alternate_url=str(employer.get("alternate_url", "")),
                vacancy_alternate_url=str(vacancy.get("alternate_url", "")),
                viewed_by_opponent=bool(raw.get("viewed_by_opponent", True)),
                salary_from=salary.get("from"),
                salary_to=salary.get("to"),
                salary_currency=salary.get("currency"),
            )
        except (KeyError, TypeError, ValueError):
            logger.exception("Failed to coerce negotiation record")
            return None
