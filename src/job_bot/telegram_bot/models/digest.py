"""Digest outcome DTO used by the slice to surface ``DailyDigestService`` results.

Mirrors the fields exposed by ``hh_applicant_tool.services.daily_digest.DigestResult``
so the slice does not need to re-define a separate DTO.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DigestOutcome:
    """Result of a single digest send attempt.

    Attributes:
        sent: whether the message was actually delivered to Telegram.
        skipped_reason: ``None`` if sent; otherwise a short code such as
            ``"already_sent"``, ``"no_chat_id"``, ``"no_telegram_config"``,
            ``"send_failed"``.
        total_drafts: number of prepared drafts considered.
        message: the rendered digest body (even if not sent).
    """

    sent: bool
    skipped_reason: str | None = None
    total_drafts: int = 0
    message: str = ""
