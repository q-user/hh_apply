"""DigestHandler -- thin orchestration around ``DailyDigestService``.

Responsibilities:
  * Forward ``send()`` to the service and surface a stable DTO.
  * Provide a time-of-day gate (``maybe_send``) that respects
    ``telegram.daily_digest_time`` and ``telegram`` config presence.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import time as dtime
from typing import Any, Mapping

from job_bot.telegram_bot.models.digest import DigestOutcome

logger = logging.getLogger(__package__)

DEFAULT_DIGEST_TIME = "10:00"


def parse_digest_time(value: str) -> dtime:
    """Parse ``"HH:MM"`` to :class:`datetime.time`; fall back to default on error."""
    try:
        parts = value.strip().split(":")
        if len(parts) != 2:
            raise ValueError
        return dtime(int(parts[0]), int(parts[1]))
    except (ValueError, AttributeError):
        logger.warning(
            "Некорректный telegram.daily_digest_time=%r, используем %s",
            value,
            DEFAULT_DIGEST_TIME,
        )
        hh, mm = DEFAULT_DIGEST_TIME.split(":")
        return dtime(int(hh), int(mm))


class DigestHandler:
    """Thin wrapper around :class:`DailyDigestService`."""

    def __init__(
        self,
        *,
        storage: Any,
        transport: Any,
        digest_service: Any,
    ) -> None:
        # ``storage`` and ``transport`` are accepted for parity with the
        # other handlers; the digest service already closes over them.
        self._storage = storage
        self._transport = transport
        self._digest = digest_service

    # ─── Direct send ──────────────────────────────────────────

    def send(self, force: bool = False) -> DigestOutcome:
        """Trigger the digest service; return a :class:`DigestOutcome`."""
        result = self._digest.send(force=force)
        return DigestOutcome(
            sent=bool(getattr(result, "sent", False)),
            skipped_reason=getattr(result, "skipped_reason", None),
            total_drafts=int(getattr(result, "total_drafts", 0)),
            message=str(getattr(result, "message", "")),
        )

    def collect_groups(self) -> list[Any]:
        """Expose the digest service's grouping for /stats previews."""
        return self._digest.collect_groups()

    # ─── Time-gated send ──────────────────────────────────────

    def maybe_send(
        self,
        *,
        config: Mapping[str, Any],
        now: datetime | None = None,
        force: bool = False,
    ) -> DigestOutcome | None:
        """Send the digest only if ``now`` is past the configured time.

        Returns ``None`` when the gate is closed (no telegram config or
        time-of-day not reached).
        """
        telegram_cfg = config.get("telegram") or {}
        if not telegram_cfg:
            logger.info("daily_digest: telegram config отсутствует — skip")
            return None

        if now is None:
            now = datetime.now()

        target_str = str(
            telegram_cfg.get("daily_digest_time", DEFAULT_DIGEST_TIME)
        )
        target = parse_digest_time(target_str)
        if now.time() < target:
            logger.debug(
                "daily_digest: время ещё не пришло (target=%s) — skip",
                target_str,
            )
            return None

        return self.send(force=force)
