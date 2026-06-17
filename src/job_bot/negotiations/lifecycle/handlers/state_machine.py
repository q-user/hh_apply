"""The state machine that decides what to do with each negotiation.

Mirrors the legacy ``clear_negotiations.clear()`` logic (issue #137).
Per negotiation, the machine answers three questions in order:

1. **Should we decline?** — the negotiation's state is
   ``refusal`` / ``discard`` *or* it's older than ``--older-than N``
   days.
2. **Should we delete the chat?** — only if the caller asked for
   ``--delete-chat``.
3. **Should we blacklist the employer?** — if the caller asked for
   ``--blacklist-discard``, or if ``--block-ats`` was set *and* the
   response came back suspiciously fast (≤ 16 minutes).

All mutations are skipped in ``dry_run`` mode, but the counters are
incremented regardless so the caller can render a summary.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from job_bot.negotiations.lifecycle.models.negotiation_state import (
    NegotiationLifecycleResult,
    NegotiationRecord,
)
from job_bot.negotiations.lifecycle.ports.api_port import LifecycleApiPort
from job_bot.shared.utils.datetime_utils import try_parse_datetime

logger = logging.getLogger(__name__)


# ATS detection threshold: a response faster than this is treated as
# automated. The legacy code used ``<= 16 * 60`` seconds.
ATS_RESPONSE_THRESHOLD_SECONDS = 16 * 60

# States that should be declined by default.
DEFAULT_DECLINE_STATES = frozenset({"refusal", "discard"})


class LifecycleStateMachine:
    """Decide what (if anything) to do with each negotiation."""

    def __init__(
        self,
        *,
        api: LifecycleApiPort,
        blacklisted_employers: set[str] | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        self._api = api
        self._blacklisted = set(blacklisted_employers or ())
        self._now = now or dt.datetime.now(dt.timezone.utc)

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

        Returns a :class:`NegotiationLifecycleResult` with the
        per-action counters. ``dry_run=True`` increments the
        counters but does not call any mutating API.
        """
        result = NegotiationLifecycleResult()
        for raw in self._api.iter_negotiations(status="all"):
            record = self._coerce(raw)
            if record is None:
                continue
            if not self._should_decline(record, older_than=older_than):
                continue
            try:
                self._handle(
                    record,
                    result=result,
                    blacklist_discard=blacklist_discard,
                    delete_chat=delete_chat,
                    block_ats=block_ats,
                    dry_run=dry_run,
                )
            except Exception:  # noqa: BLE001
                # Per-negotiation errors are logged and counted;
                # one broken negotiation doesn't kill the run.
                result.failed += 1
                logger.exception("Failed to process negotiation %s", record.id)
        return result

    # ─── Internals ───────────────────────────────────────────────

    def _handle(
        self,
        record: NegotiationRecord,
        *,
        result: NegotiationLifecycleResult,
        blacklist_discard: bool,
        delete_chat: bool,
        block_ats: bool,
        dry_run: bool,
    ) -> None:
        # 1) Decline
        with_decline_message = record.state_id != "discard"
        if not dry_run:
            self._api.decline_negotiation(
                record.id,
                with_decline_message=with_decline_message,
            )
        result.declined += 1
        logger.debug("Declined negotiation %s", record.id)

        # 2) Delete the chat
        if delete_chat:
            if not dry_run:
                ok = self._api.delete_chat(record.id)
                if ok:
                    result.chats_deleted += 1
                    logger.debug("Deleted chat for %s", record.id)
                else:
                    logger.debug(
                        "Chat deletion returned False for %s", record.id
                    )
            else:
                result.chats_deleted += 1

        # 3) ATS detection + optional block
        seconds = record.response_seconds()
        ats_detected = (
            seconds is not None and seconds <= ATS_RESPONSE_THRESHOLD_SECONDS
        )
        if ats_detected:
            result.ats_detected += 1
            logger.info("ATS detected for negotiation %s", record.id)

        should_blacklist = blacklist_discard or (block_ats and ats_detected)
        if should_blacklist and self._can_blacklist(record):
            if not dry_run:
                self._api.blacklist_employer(record.employer_id)  # type: ignore[arg-type]
                self._blacklisted.add(record.employer_id)  # type: ignore[arg-type]
            result.blacklisted += 1

    def _should_decline(
        self, record: NegotiationRecord, *, older_than: int | None
    ) -> bool:
        if older_than is not None:
            return self._is_older_than(record, older_than)
        return record.state_id in DEFAULT_DECLINE_STATES

    def _is_older_than(self, record: NegotiationRecord, days: int) -> bool:
        try:
            updated = try_parse_datetime(record.updated_at)
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(updated, dt.datetime):
            return False
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=dt.timezone.utc)
        delta = self._now - updated
        return delta.days > days

    def _can_blacklist(self, record: NegotiationRecord) -> bool:
        if not record.has_employer:
            return False
        assert record.employer_id is not None  # for type-checkers
        return record.employer_id not in self._blacklisted

    @staticmethod
    def _coerce(raw: Any) -> NegotiationRecord | None:
        if isinstance(raw, NegotiationRecord):
            return raw
        if not isinstance(raw, dict):
            return None
        try:
            vacancy = raw.get("vacancy") or {}
            employer = vacancy.get("employer") or {}
            state = raw.get("state") or {}
            return NegotiationRecord(
                id=str(raw.get("id", "")),
                state_id=str(state.get("id", "")),
                created_at=str(raw.get("created_at", "")),
                updated_at=str(raw.get("updated_at", "")),
                employer_id=(
                    str(employer["id"])
                    if employer.get("id") is not None
                    else None
                ),
                employer_name=str(employer.get("name", "")),
                employer_alternate_url=str(employer.get("alternate_url", "")),
                vacancy_name=str(vacancy.get("name", "")),
                vacancy_alternate_url=str(vacancy.get("alternate_url", "")),
            )
        except (KeyError, TypeError, ValueError):
            logger.exception("Failed to coerce negotiation record")
            return None
