"""``ChannelPoller`` -- poll a single Telegram channel for new messages (issue #61).

The poller is intentionally minimal: it owns no transport of its own
(transport is injected via the port), no scheduling (the
:class:`ChannelMonitorService` drives the loop), and no notification
(it returns the new links; the caller decides what to do with them).

Wire format: the Telegram Bot API delivers channel posts as
``update.channel_post`` updates. We filter updates whose ``chat.id``
matches the configured channel (the ``Channel.channel_id`` field may be
``"@name"`` or a numeric id; we normalise both sides to strings before
comparison) and whose text passes the optional keyword filter. Vacancy
links are extracted via :class:`ChannelHandler.parse_message` and
deduplicated against the ``cm_vacancy_links`` table.
"""

from __future__ import annotations

import logging
from typing import Any

from job_bot.channel_monitoring.handlers.channel_handler import ChannelHandler
from job_bot.channel_monitoring.models.channel import Channel
from job_bot.channel_monitoring.models.vacancy_link import VacancyLink
from job_bot.telegram_bot.ports.transport_port import TelegramTransportPort

logger = logging.getLogger(__name__)


class ChannelPoller:
    """Poll a single :class:`Channel` and return new vacancy links.

    Args:
        transport: a :class:`TelegramTransportPort` (the same one used
            by the Telegram bot slice -- typically a
            :class:`TelegramTransport` from
            ``hh_applicant_tool.telegram.transport``).
        channel: the :class:`Channel` to poll.
        handler: a :class:`ChannelHandler` for persistence + parsing.
    """

    def __init__(
        self,
        *,
        transport: TelegramTransportPort,
        channel: Channel,
        handler: ChannelHandler,
    ) -> None:
        self._transport = transport
        self._channel = channel
        self._handler = handler

    @property
    def channel(self) -> Channel:
        return self._channel

    def poll_once(
        self, *, offset: int | None = None
    ) -> tuple[list[VacancyLink], int]:
        """Fetch new messages from the channel.

        Args:
            offset: the ``update_id`` to start from (exclusive). When
                ``None`` the poller uses the channel's stored
                ``last_message_id`` as the starting point.

        Returns:
            ``(new_links, next_offset)`` where ``new_links`` are vacancy
            links that pass the keyword filter and are not already in
            the dedup table, and ``next_offset`` is the value the caller
            should pass to the next call (or persist as
            ``last_message_id``).
        """
        start_offset = (
            offset
            if offset is not None
            else max(self._channel.last_message_id, 0)
        )
        # ``+ 1`` because Telegram's getUpdates is inclusive of the
        # offset, so we want strictly newer updates.
        updates = self._transport.get_updates(
            offset=start_offset + 1,
        )
        next_offset = start_offset
        new_links: list[VacancyLink] = []

        for update in updates:
            update_id = int(update.get("update_id", 0) or 0)
            if update_id > next_offset:
                next_offset = update_id

            post = update.get("channel_post") or update.get("message")
            if not isinstance(post, dict):
                continue

            chat = post.get("chat") or {}
            chat_id = chat.get("id")
            if not self._chat_matches(chat_id, self._channel.channel_id):
                continue

            text = str(post.get("text") or "")
            message_id = int(post.get("message_id", 0) or 0)

            if not self._passes_keyword_filter(
                text, self._channel.filter_keywords
            ):
                logger.debug(
                    "ChannelPoller[%s]: message %d filtered by keywords",
                    self._channel.channel_id,
                    message_id,
                )
                continue

            for link in self._handler.parse_message(
                text, self._channel.channel_id, message_id
            ):
                if self._handler.is_already_processed(link.vacancy_id):
                    continue
                new_links.append(link)
        return new_links, next_offset

    @staticmethod
    def _chat_matches(observed: Any, configured: str) -> bool:
        """Return ``True`` if the observed chat id matches the configured one.

        Accepts both the ``"@name"`` form and the numeric id form on
        either side -- we normalise both to ``"name"`` / ``"12345"``
        before comparison so a user can configure ``"@vacancies"`` and
        still match an observed ``"@vacancies"`` or numeric id.
        """
        if observed is None:
            return False
        target = str(configured).lstrip("@")
        observed_norm = str(observed).lstrip("@")
        return observed_norm == target

    @staticmethod
    def _passes_keyword_filter(text: str, keywords: list[str]) -> bool:
        """Return ``True`` if ``text`` passes the keyword filter.

        An empty keyword list means "accept all". Matching is
        case-insensitive substring match. ``None`` entries in the
        keyword list are ignored.
        """
        if not keywords:
            return True
        haystack = text.lower()
        for kw in keywords:
            if kw and kw.lower() in haystack:
                return True
        return False
