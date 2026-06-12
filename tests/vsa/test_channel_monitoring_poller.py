"""Tests for the real channel monitoring poller + service (issue #61).

Covers:
* ``ChannelPoller``: offset handling, keyword filter, dedup, channel-id matching
  (both ``"@name"`` and numeric id forms).
* ``ChannelMonitorService.tick``: per-channel isolation, notification fan-out,
  ``last_message_id`` persistence, notifier failures don't break the loop.
* ``NullNotificationPort``: stores delivered links for assertions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from job_bot.channel_monitoring.handlers.channel_handler import ChannelHandler
from job_bot.channel_monitoring.models.channel import ChannelCreate
from job_bot.channel_monitoring.ports.notification_port import (
    NullNotificationPort,
)
from job_bot.channel_monitoring.services.channel_poller import ChannelPoller
from job_bot.channel_monitoring.services.monitor_service import (
    ChannelMonitorService,
    create_channel_monitor_service,
)


# ─── Helpers ────────────────────────────────────────────────────────


def _make_channel_update(
    update_id: int,
    *,
    chat_id: int | str,
    text: str,
    message_id: int = 1,
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "channel_post": {
            "message_id": message_id,
            "chat": {"id": chat_id, "type": "channel"},
            "text": text,
        },
    }


def _make_transport(updates: list[dict[str, Any]] | None = None) -> MagicMock:
    transport = MagicMock()
    transport.get_updates = MagicMock(return_value=updates or [])
    transport.send_message = MagicMock()
    return transport


# ─── ChannelPoller ──────────────────────────────────────────────────


class TestChannelPoller:
    """``ChannelPoller`` polls a single channel for new messages."""

    def _make_poller(
        self,
        *,
        storage_conn: Any,
        transport: Any,
        channel_id: str = "@vacancies",
        last_message_id: int = 0,
        keywords: list[str] | None = None,
    ) -> tuple[ChannelPoller, ChannelHandler]:
        handler = ChannelHandler(storage_conn)
        handler.add_channel(
            ChannelCreate(
                name="Vac",
                channel_id=channel_id,
                filter_keywords=keywords or [],
            ),
        )
        # Pin the last_message_id directly (the slice would have set it
        # from a prior poll).
        handler.update_last_message_id(channel_id, last_message_id)
        channel = handler.get_channel(channel_id)
        assert channel is not None
        return (
            ChannelPoller(
                transport=transport, channel=channel, handler=handler
            ),
            handler,
        )

    def test_returns_empty_when_no_updates(self, storage_conn: Any) -> None:
        transport = _make_transport(updates=[])
        poller, _ = self._make_poller(
            storage_conn=storage_conn, transport=transport
        )
        new_links, next_offset = poller.poll_once()
        assert new_links == []
        assert next_offset == 0
        transport.get_updates.assert_called_once()

    def test_extracts_vacancy_link(self, storage_conn: Any) -> None:
        transport = _make_transport(
            updates=[
                _make_channel_update(
                    1,
                    chat_id="@vacancies",
                    text="See https://hh.ru/vacancy/123",
                ),
            ]
        )
        poller, _ = self._make_poller(
            storage_conn=storage_conn,
            transport=transport,
            channel_id="@vacancies",
        )
        new_links, next_offset = poller.poll_once()
        assert len(new_links) == 1
        assert new_links[0].vacancy_id == "123"
        assert new_links[0].source_channel == "@vacancies"
        assert next_offset == 1

    def test_filters_by_keyword(self, storage_conn: Any) -> None:
        transport = _make_transport(
            updates=[
                _make_channel_update(
                    1,
                    chat_id="@vacancies",
                    text="See https://hh.ru/vacancy/123",
                ),
                _make_channel_update(
                    2,
                    chat_id="@vacancies",
                    text="https://hh.ru/vacancy/999",
                ),
            ]
        )
        poller, _ = self._make_poller(
            storage_conn=storage_conn,
            transport=transport,
            keywords=["python"],
        )
        # First update doesn't match "python" -> filtered.
        # Second update: contains "python" (case-insensitive substring) -> match.
        # Note: the second URL doesn't contain "python" in its text, so it
        # will also be filtered. We use a text that explicitly mentions python.
        new_links, _ = poller.poll_once()
        assert new_links == []

    def test_keyword_filter_accepts_matching_text(
        self, storage_conn: Any
    ) -> None:
        transport = _make_transport(
            updates=[
                _make_channel_update(
                    1,
                    chat_id="@vacancies",
                    text="python role: https://hh.ru/vacancy/123",
                ),
            ]
        )
        poller, _ = self._make_poller(
            storage_conn=storage_conn,
            transport=transport,
            keywords=["python"],
        )
        new_links, _ = poller.poll_once()
        assert len(new_links) == 1
        assert new_links[0].vacancy_id == "123"

    def test_keyword_filter_is_case_insensitive(
        self, storage_conn: Any
    ) -> None:
        transport = _make_transport(
            updates=[
                _make_channel_update(
                    1,
                    chat_id="@vacancies",
                    text="PYTHON role: https://hh.ru/vacancy/1",
                ),
            ]
        )
        poller, _ = self._make_poller(
            storage_conn=storage_conn,
            transport=transport,
            keywords=["Python"],
        )
        new_links, _ = poller.poll_once()
        assert len(new_links) == 1

    def test_ignores_other_channels(self, storage_conn: Any) -> None:
        transport = _make_transport(
            updates=[
                _make_channel_update(
                    1,
                    chat_id=999_999,  # different channel
                    text="https://hh.ru/vacancy/1",
                ),
            ]
        )
        poller, _ = self._make_poller(
            storage_conn=storage_conn,
            transport=transport,
            channel_id="@vacancies",
        )
        new_links, _ = poller.poll_once()
        assert new_links == []

    def test_numeric_chat_id_match(self, storage_conn: Any) -> None:
        transport = _make_transport(
            updates=[
                _make_channel_update(
                    1,
                    chat_id=12345,
                    text="https://hh.ru/vacancy/1",
                ),
            ]
        )
        poller, _ = self._make_poller(
            storage_conn=storage_conn,
            transport=transport,
            channel_id="12345",
        )
        new_links, _ = poller.poll_once()
        assert len(new_links) == 1

    def test_skips_already_processed(self, storage_conn: Any) -> None:
        from job_bot.channel_monitoring.models.vacancy_link import VacancyLink

        handler = ChannelHandler(storage_conn)
        handler.add_channel(ChannelCreate(name="Vac", channel_id="@vacancies"))
        # Pre-populate dedup.
        handler.mark_processed(
            VacancyLink(
                url="https://hh.ru/vacancy/1",
                vacancy_id="1",
                source_channel="@vacancies",
                message_id=99,
            )
        )
        channel = handler.get_channel("@vacancies")
        assert channel is not None
        transport = _make_transport(
            updates=[
                _make_channel_update(
                    1,
                    chat_id="@vacancies",
                    text="https://hh.ru/vacancy/1",
                ),
            ]
        )
        poller = ChannelPoller(
            transport=transport, channel=channel, handler=handler
        )
        new_links, _ = poller.poll_once()
        assert new_links == []

    def test_offset_advances_from_last_message_id(
        self, storage_conn: Any
    ) -> None:
        transport = _make_transport(updates=[])
        poller, _ = self._make_poller(
            storage_conn=storage_conn,
            transport=transport,
            last_message_id=42,
        )
        poller.poll_once()
        # The poller should pass ``last_message_id + 1`` as the offset
        # to the transport.
        call_kwargs = transport.get_updates.call_args.kwargs
        assert (
            call_kwargs.get("offset") == 43
            or transport.get_updates.call_args.args[0] == 43
        )

    def test_explicit_offset_overrides_last_message_id(
        self, storage_conn: Any
    ) -> None:
        transport = _make_transport(updates=[])
        poller, _ = self._make_poller(
            storage_conn=storage_conn,
            transport=transport,
            last_message_id=42,
        )
        poller.poll_once(offset=100)
        # 100 + 1 = 101 should be the offset passed to getUpdates.
        call_kwargs = transport.get_updates.call_args.kwargs
        assert (
            call_kwargs.get("offset") == 101
            or transport.get_updates.call_args.args[0] == 101
        )


# ─── ChannelMonitorService ──────────────────────────────────────────


class TestChannelMonitorService:
    """``ChannelMonitorService`` orchestrates polling all enabled channels."""

    def _make_service(
        self,
        *,
        storage_conn: Any,
        updates: list[dict[str, Any]] | None = None,
        chat_id: int | None = 42,
        transport: Any | None = None,
        notifier: Any | None = None,
    ) -> tuple[ChannelMonitorService, ChannelHandler, NullNotificationPort]:
        handler = ChannelHandler(storage_conn)
        handler.add_channel(ChannelCreate(name="Vac", channel_id="@vacancies"))
        notif = notifier or NullNotificationPort()
        trans = transport or _make_transport(updates=updates)
        service = create_channel_monitor_service(
            transport=trans,
            handler=handler,
            notifier=notif,
            tick_interval=0,
            chat_id=chat_id,
        )
        return service, handler, notif

    def test_tick_delivers_new_links(self, storage_conn: Any) -> None:
        updates = [
            _make_channel_update(
                1, chat_id="@vacancies", text="https://hh.ru/vacancy/1"
            ),
            _make_channel_update(
                2, chat_id="@vacancies", text="https://hh.ru/vacancy/2"
            ),
        ]
        service, handler, notif = self._make_service(
            storage_conn=storage_conn, updates=updates
        )
        delivered = service.tick()
        assert delivered == 2
        assert len(notif.sent) == 2
        assert {link.vacancy_id for _, link in notif.sent} == {"1", "2"}
        # Both should be marked processed.
        assert handler.is_already_processed("1")
        assert handler.is_already_processed("2")

    def test_tick_persists_last_message_id(self, storage_conn: Any) -> None:
        updates = [
            _make_channel_update(
                5, chat_id="@vacancies", text="https://hh.ru/vacancy/1"
            ),
        ]
        service, handler, _ = self._make_service(
            storage_conn=storage_conn, updates=updates
        )
        service.tick()
        channel = handler.get_channel("@vacancies")
        assert channel is not None
        assert channel.last_message_id == 5

    def test_tick_isolates_channel_failures(self, storage_conn: Any) -> None:
        """A poll failure on one channel must not stop the others."""
        handler = ChannelHandler(storage_conn)
        handler.add_channel(ChannelCreate(name="A", channel_id="@a"))
        handler.add_channel(ChannelCreate(name="B", channel_id="@b"))
        notif = NullNotificationPort()
        transport = MagicMock()

        # First call (channel @a) raises; second call (channel @b) returns updates.
        call_count = {"n": 0}

        def _get_updates(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("transport down")
            return [
                _make_channel_update(
                    1, chat_id="@b", text="https://hh.ru/vacancy/1"
                ),
            ]

        transport.get_updates = MagicMock(side_effect=_get_updates)
        service = create_channel_monitor_service(
            transport=transport,
            handler=handler,
            notifier=notif,
            tick_interval=0,
            chat_id=42,
        )
        delivered = service.tick()
        assert delivered == 1
        # Only the second channel's update was delivered.
        assert len(notif.sent) == 1
        assert notif.sent[0][1].vacancy_id == "1"

    def test_tick_swallows_notifier_failures(self, storage_conn: Any) -> None:
        """A raising notifier MUST NOT break the tick loop."""
        handler = ChannelHandler(storage_conn)
        handler.add_channel(ChannelCreate(name="Vac", channel_id="@vac"))
        notifier = MagicMock()
        notifier.send.side_effect = RuntimeError("telegram down")
        transport = _make_transport(
            updates=[
                _make_channel_update(
                    1, chat_id="@vac", text="https://hh.ru/vacancy/1"
                ),
            ]
        )
        service = create_channel_monitor_service(
            transport=transport,
            handler=handler,
            notifier=notifier,
            tick_interval=0,
            chat_id=42,
        )
        # Must not raise.
        delivered = service.tick()
        assert delivered == 1
        notifier.send.assert_called_once()

    def test_tick_skips_disabled_channels(self, storage_conn: Any) -> None:
        handler = ChannelHandler(storage_conn)
        handler.add_channel(
            ChannelCreate(name="Off", channel_id="@off", enabled=False)
        )
        notif = NullNotificationPort()
        transport = _make_transport(
            updates=[
                _make_channel_update(
                    1, chat_id="@off", text="https://hh.ru/vacancy/1"
                ),
            ]
        )
        service = create_channel_monitor_service(
            transport=transport,
            handler=handler,
            notifier=notif,
            tick_interval=0,
            chat_id=42,
        )
        delivered = service.tick()
        assert delivered == 0
        assert notif.sent == []

    def test_run_stops_after_n_ticks(self, storage_conn: Any) -> None:
        """``run(stop_after=N)`` returns after N ticks."""
        service, _, _ = self._make_service(
            storage_conn=storage_conn, updates=[]
        )
        # tick_interval=0 so the loop is fast.
        total = service.run(stop_after=3)
        assert total == 0  # no updates

    def test_run_returns_total_delivered(self, storage_conn: Any) -> None:
        """``run`` returns the cumulative delivered count across ticks."""
        service, _, notif = self._make_service(
            storage_conn=storage_conn,
            updates=[
                _make_channel_update(
                    1, chat_id="@vacancies", text="https://hh.ru/vacancy/1"
                ),
            ],
        )
        total = service.run(stop_after=1)
        assert total == 1
        assert len(notif.sent) == 1


# ─── NullNotificationPort ───────────────────────────────────────────


class TestNullNotificationPort:
    def test_records_delivery(self) -> None:
        from job_bot.channel_monitoring.models.vacancy_link import VacancyLink

        notif = NullNotificationPort()
        link = VacancyLink(
            url="https://hh.ru/vacancy/1",
            vacancy_id="1",
            source_channel="@x",
            message_id=1,
        )
        notif.send(42, link)
        assert notif.last_sent == (42, link)
        assert notif.sent == [(42, link)]

    def test_satisfies_port_protocol(self) -> None:
        from job_bot.channel_monitoring.ports.notification_port import (
            NotificationPort,
        )

        notif: NotificationPort = NullNotificationPort()
        assert notif is not None
