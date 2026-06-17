"""Tests for the channel_monitoring slice (TDD pilot).

Covers:
- ChannelModel DTOs (Channel, ChannelCreate)
- VacancyLinkModel DTO (VacancyLink)
- ChannelHandler CRUD (add/remove/list/get)
- ChannelHandler.parse_message (extract vacancy links from text)
- ChannelHandler.is_already_processed (deduplication)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from job_bot.channel_monitoring.handlers.channel_handler import ChannelHandler
from job_bot.channel_monitoring.models.channel import (
    Channel,
    ChannelCreate,
)
from job_bot.channel_monitoring.models.vacancy_link import VacancyLink
from job_bot.channel_monitoring.ports.channel_port import ChannelPort


class TestChannelModel:
    """ChannelModel DTO tests."""

    def test_channel_defaults(self) -> None:
        ch = Channel(name="Test", channel_id="@test")
        assert ch.id  # auto-generated uuid
        assert ch.name == "Test"
        assert ch.channel_id == "@test"
        assert ch.enabled is True
        assert ch.last_message_id == 0
        assert ch.filter_keywords == []
        assert isinstance(ch.created_at, datetime)

    def test_channel_create_to_channel(self) -> None:
        create = ChannelCreate(
            name="Vacancies",
            channel_id="@vacancies",
            filter_keywords=["python", "django"],
        )
        ch = create.to_channel()
        assert ch.name == "Vacancies"
        assert ch.channel_id == "@vacancies"
        assert ch.filter_keywords == ["python", "django"]
        assert ch.enabled is True


class TestVacancyLinkModel:
    """VacancyLinkModel DTO tests."""

    def test_vacancy_link_creation(self) -> None:
        link = VacancyLink(
            url="https://hh.ru/vacancy/12345",
            vacancy_id="12345",
            source_channel="@vacancies",
            message_id=42,
        )
        assert link.url == "https://hh.ru/vacancy/12345"
        assert link.vacancy_id == "12345"
        assert link.source_channel == "@vacancies"
        assert link.message_id == 42
        assert isinstance(link.created_at, datetime)

    def test_vacancy_link_default_timestamp(self) -> None:
        before = datetime.now()
        link = VacancyLink(
            url="https://hh.ru/vacancy/1",
            vacancy_id="1",
            source_channel="@x",
            message_id=1,
        )
        assert link.created_at >= before


class TestChannelHandlerCRUD:
    """ChannelHandler CRUD operations."""

    @pytest.fixture
    def handler(self, storage_conn: Any) -> ChannelHandler:
        return ChannelHandler(storage_conn)

    def test_add_channel_returns_entity(self, handler: ChannelHandler) -> None:
        ch = handler.add_channel(ChannelCreate(name="Vac", channel_id="@vac"))
        assert ch.id
        assert ch.name == "Vac"
        assert ch.channel_id == "@vac"

    def test_add_channel_persists(self, handler: ChannelHandler) -> None:
        handler.add_channel(ChannelCreate(name="Vac", channel_id="@vac"))
        assert handler.get_channel("@vac") is not None

    def test_get_channel_missing_returns_none(
        self, handler: ChannelHandler
    ) -> None:
        assert handler.get_channel("@missing") is None

    def test_list_channels_returns_all(self, handler: ChannelHandler) -> None:
        handler.add_channel(ChannelCreate(name="A", channel_id="@a"))
        handler.add_channel(ChannelCreate(name="B", channel_id="@b"))
        channels = handler.list_channels()
        ids = {c.channel_id for c in channels}
        assert ids == {"@a", "@b"}

    def test_list_channels_enabled_only(self, handler: ChannelHandler) -> None:
        handler.add_channel(
            ChannelCreate(name="A", channel_id="@a", enabled=True)
        )
        handler.add_channel(
            ChannelCreate(name="B", channel_id="@b", enabled=False)
        )
        enabled = handler.list_channels(enabled_only=True)
        assert {c.channel_id for c in enabled} == {"@a"}

    def test_remove_channel_returns_true_when_exists(
        self, handler: ChannelHandler
    ) -> None:
        handler.add_channel(ChannelCreate(name="A", channel_id="@a"))
        assert handler.remove_channel("@a") is True
        assert handler.get_channel("@a") is None

    def test_remove_channel_returns_false_when_missing(
        self, handler: ChannelHandler
    ) -> None:
        assert handler.remove_channel("@missing") is False


class TestChannelHandlerParseMessage:
    """ChannelHandler.parse_message behaviour."""

    @pytest.fixture
    def handler(self, storage_conn: Any) -> ChannelHandler:
        return ChannelHandler(storage_conn)

    def test_parse_message_extracts_single_link(
        self, handler: ChannelHandler
    ) -> None:
        text = "Apply here: https://hh.ru/vacancy/12345"
        links = handler.parse_message(text, "@src", 1)
        assert len(links) == 1
        assert links[0].vacancy_id == "12345"
        assert links[0].url == "https://hh.ru/vacancy/12345"
        assert links[0].source_channel == "@src"
        assert links[0].message_id == 1

    def test_parse_message_extracts_multiple_links(
        self, handler: ChannelHandler
    ) -> None:
        text = (
            "Two openings: https://hh.ru/vacancy/111 "
            "and https://spb.hh.ru/vacancy/222"
        )
        links = handler.parse_message(text, "@src", 7)
        ids = {link.vacancy_id for link in links}
        assert ids == {"111", "222"}

    def test_parse_message_returns_empty_when_no_links(
        self, handler: ChannelHandler
    ) -> None:
        assert handler.parse_message("hello world", "@src", 1) == []

    def test_parse_message_ignores_non_hh_urls(
        self, handler: ChannelHandler
    ) -> None:
        text = "see https://example.com/vacancy/999"
        assert handler.parse_message(text, "@src", 1) == []


class TestChannelHandlerDedup:
    """ChannelHandler.is_already_processed behaviour."""

    @pytest.fixture
    def handler(self, storage_conn: Any) -> ChannelHandler:
        return ChannelHandler(storage_conn)

    def test_unseen_vacancy_returns_false(
        self, handler: ChannelHandler
    ) -> None:
        assert handler.is_already_processed("99999") is False

    def test_seen_vacancy_returns_true(self, handler: ChannelHandler) -> None:
        link = VacancyLink(
            url="https://hh.ru/vacancy/12345",
            vacancy_id="12345",
            source_channel="@src",
            message_id=1,
        )
        handler.mark_processed(link)
        assert handler.is_already_processed("12345") is True

    def test_dedup_after_parse_message(self, handler: ChannelHandler) -> None:
        text = "https://hh.ru/vacancy/42"
        links = handler.parse_message(text, "@src", 1)
        assert handler.is_already_processed("42") is False
        for link in links:
            handler.mark_processed(link)
        assert handler.is_already_processed("42") is True


class TestChannelMonitoringSlice:
    """End-to-end slice integration."""

    def test_satisfies_port_protocol(self, storage_conn: Any) -> None:
        handler = ChannelHandler(storage_conn)
        # ChannelHandler must be a valid ChannelPort implementation.
        port: ChannelPort = handler
        port.add_channel(ChannelCreate(name="A", channel_id="@a"))
        assert port.get_channel("@a") is not None
        assert port.is_already_processed("1") is False
        assert port.parse_message("https://hh.ru/vacancy/1", "@a", 1)
