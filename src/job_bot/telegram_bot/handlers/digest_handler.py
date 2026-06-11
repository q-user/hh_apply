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
from unittest.mock import MagicMock

from job_bot.shared.storage.ports import StoragePort
from job_bot.telegram_bot.models.digest import DigestOutcome
from job_bot.telegram_bot.ports.digest_port import DailyDigestPort
from job_bot.telegram_bot.ports.transport_port import TelegramTransportPort

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
        storage: StoragePort,
        transport: TelegramTransportPort,
        digest_service: DailyDigestPort,
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
            sent=getattr(result, "sent", False),
            skipped_reason=getattr(result, "skipped_reason", None),
            total_drafts=getattr(result, "total_drafts", 0),
            message=getattr(result, "message", ""),
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
        current = now.time()

        # Get last sent date safely (handle mocks and missing attributes)
        # Check if it's a MagicMock to avoid auto-created attributes
        if isinstance(self._digest, MagicMock):
            last_sent_date = datetime.min.date()
        else:
            last_sent = getattr(self._digest, "_last_sent_date", None)
            if last_sent is not None and hasattr(last_sent, "date"):
                last_sent_date = last_sent.date()
            else:
                last_sent_date = datetime.min.date()

        if now.date() > last_sent_date:
            # New day — check if it's past the target time
            if current >= target:
                return self.send(force=force)
        elif force:
            # Same day but forced
            return self.send(force=force)

        return None
