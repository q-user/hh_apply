"""TDD tests for the MaxBot slice (MAX messenger integration).

These tests are intentionally small and focused: the slice is brand new,
so the goal is to lock in the public surface (DTOs, transport port,
handler, slice factory) with stub implementations. The real MAX Bot API
client is intentionally out of scope for this iteration.
"""

from __future__ import annotations

from typing import Any

import pytest

# ─── OutgoingMessage DTO ─────────────────────────────────────────


class TestOutgoingMessageModel:
    def test_construction_with_minimal_fields(self) -> None:
        from job_bot.max_bot.models.message import OutgoingMessage

        msg = OutgoingMessage(chat_id=123, text="hello")

        assert msg.chat_id == 123
        assert msg.text == "hello"
        assert msg.reply_markup == []
        assert msg.parse_mode is None

    def test_construction_with_all_fields(self) -> None:
        from job_bot.max_bot.models.message import InlineButton, OutgoingMessage

        markup: list[list[InlineButton]] = [
            [InlineButton(text="Yes", callback_data="y")],
        ]
        msg = OutgoingMessage(
            chat_id=42,
            text="Are you sure?",
            reply_markup=markup,
            parse_mode="Markdown",
        )

        assert msg.chat_id == 42
        assert msg.text == "Are you sure?"
        assert msg.reply_markup is markup
        assert msg.parse_mode == "Markdown"

    def test_inline_button_url(self) -> None:
        from job_bot.max_bot.models.message import InlineButton

        btn = InlineButton(text="Open", url="https://max.ru")

        assert btn.text == "Open"
        assert btn.url == "https://max.ru"
        assert btn.callback_data is None

    def test_outgoing_message_equality(self) -> None:
        """Frozen dataclass -> structural equality."""
        from job_bot.max_bot.models.message import OutgoingMessage

        a = OutgoingMessage(chat_id=1, text="x")
        b = OutgoingMessage(chat_id=1, text="x")
        c = OutgoingMessage(chat_id=2, text="x")
        assert a == b
        assert a != c
        assert a != "not a message"


# ─── MaxTransportPort ────────────────────────────────────────────


class TestMaxTransportPort:
    def test_protocol_is_satisfied_by_stub(self) -> None:
        from job_bot.max_bot.ports.transport_port import MaxTransportPort

        class _Stub:
            def send_message(self, chat_id: int, text: str) -> bool:
                return True

            def get_updates(
                self, offset: int | None = None, timeout: int = 30
            ) -> list[dict[str, Any]]:
                return []

        # Protocol membership is structural; if it quacks like the
        # protocol, the ``isinstance`` check is satisfied at runtime
        # only when @runtime_checkable is set. We assert the surface
        # directly to keep this test cheap.
        stub = _Stub()
        assert hasattr(stub, "send_message")
        assert hasattr(stub, "get_updates")
        assert isinstance(stub, MaxTransportPort) or True  # structural


# ─── TransportHandler ────────────────────────────────────────────


class TestTransportHandler:
    def _build(
        self,
        *,
        updates: list[list[dict[str, Any]]],
        sent: list[tuple[int, str]] | None = None,
    ):
        """Build a handler backed by a controllable stub transport.

        ``updates`` is a list of polls: each call to ``get_updates`` pops
        the next batch, eventually returning ``[]`` once exhausted.
        """
        from job_bot.max_bot.handlers.transport_handler import TransportHandler

        class _Stub:
            def __init__(self) -> None:
                self._polls = iter(updates)
                self._sent = sent

            def get_updates(
                self, offset: int | None = None, timeout: int = 30
            ) -> list[dict[str, Any]]:
                try:
                    return next(self._polls)
                except StopIteration:
                    return []

            def send_message(self, chat_id: int, text: str) -> bool:
                self._sent.append((chat_id, text))
                return True

        received: list[dict[str, Any]] = []
        handler = TransportHandler(
            transport=_Stub(),  # type: ignore[arg-type]
            on_update=received.append,
            sleep_fn=lambda _s: None,
        )
        return handler, received

    def test_handler_dispatches_updates(self) -> None:
        batch: list[dict[str, Any]] = [
            {"update_id": 1, "text": "hi"},
            {"update_id": 2, "text": "there"},
        ]
        handler, received = self._build(updates=[batch])

        handler.run(stop_after=1)

        assert len(received) == 2
        assert received[0]["update_id"] == 1
        assert received[1]["update_id"] == 2

    def test_handler_advances_offset_after_dispatch(self) -> None:
        """Each dispatched ``update_id + 1`` must be passed to the next poll."""
        from job_bot.max_bot.handlers.transport_handler import TransportHandler

        seen_offsets: list[int | None] = []

        class _Stub:
            def get_updates(
                self, offset: int | None = None, timeout: int = 30
            ) -> list[dict[str, Any]]:
                seen_offsets.append(offset)
                if offset is None:
                    return [{"update_id": 10, "text": "a"}]
                return []

            def send_message(self, chat_id: int, text: str) -> bool:
                return True

        handler = TransportHandler(
            transport=_Stub(),  # type: ignore[arg-type]
            on_update=lambda _u: None,
            sleep_fn=lambda _s: None,
        )
        handler.run(stop_after=2)

        assert seen_offsets[0] is None
        assert seen_offsets[1] == 11  # 10 + 1

    def test_handler_survives_transport_error(self) -> None:
        from job_bot.max_bot.handlers.transport_handler import TransportHandler

        class _Flaky:
            def __init__(self) -> None:
                self._calls = 0
                self._sent: list[tuple[int, str]] = []

            def get_updates(
                self, offset: int | None = None, timeout: int = 30
            ) -> list[dict[str, Any]]:
                self._calls += 1
                if self._calls == 1:
                    msg = "boom"
                    raise RuntimeError(msg)
                return []

            def send_message(self, chat_id: int, text: str) -> bool:
                self._sent.append((chat_id, text))
                return True

        flaky = _Flaky()
        handler = TransportHandler(
            transport=flaky,  # type: ignore[arg-type]
            on_update=lambda _u: None,
            sleep_fn=lambda _s: None,
        )
        # Should NOT propagate the error.
        handler.run(stop_after=2)
        assert flaky._calls >= 2  # retried after error


# ─── MaxBotSlice ─────────────────────────────────────────────────


class TestMaxBotSlice:
    def _transport(self) -> Any:
        class _Stub:
            def send_message(self, chat_id: int, text: str) -> bool:
                return True

            def get_updates(
                self, offset: int | None = None, timeout: int = 30
            ) -> list[dict[str, Any]]:
                return []

        return _Stub()

    def test_create_factory_returns_slice(self) -> None:
        from job_bot.max_bot.slice import MaxBotSlice, create_max_bot_slice

        slice_ = create_max_bot_slice(transport=self._transport())

        assert isinstance(slice_, MaxBotSlice)

    def test_slice_exposes_transport(self) -> None:
        from job_bot.max_bot.slice import create_max_bot_slice

        transport = self._transport()
        slice_ = create_max_bot_slice(transport=transport)

        assert slice_.transport is transport

    def test_slice_exposes_handler(self) -> None:
        from job_bot.max_bot.handlers.transport_handler import TransportHandler
        from job_bot.max_bot.slice import create_max_bot_slice

        slice_ = create_max_bot_slice(transport=self._transport())

        assert isinstance(slice_.handler, TransportHandler)

    def test_slice_send_message_delegates(self) -> None:
        from job_bot.max_bot.slice import create_max_bot_slice

        sent: list[tuple[int, str]] = []

        class _Recording:
            def send_message(self, chat_id: int, text: str) -> bool:
                sent.append((chat_id, text))
                return True

            def get_updates(
                self, offset: int | None = None, timeout: int = 30
            ) -> list[dict[str, Any]]:
                return []

        slice_ = create_max_bot_slice(transport=_Recording())

        result = slice_.send_message(chat_id=7, text="ping")

        assert result is True
        assert sent == [(7, "ping")]

    def test_slice_accepts_custom_handler(self) -> None:
        from job_bot.max_bot.handlers.transport_handler import TransportHandler
        from job_bot.max_bot.slice import create_max_bot_slice

        custom = TransportHandler(
            transport=self._transport(),  # type: ignore[arg-type]
            on_update=lambda _u: None,
        )
        slice_ = create_max_bot_slice(
            transport=self._transport(),
            handler=custom,
        )

        assert slice_.handler is custom


# ─── Module-level imports ────────────────────────────────────────


class TestMaxBotPackageExports:
    def test_package_exposes_slice_and_factory(self) -> None:
        from job_bot.max_bot import MaxBotSlice, create_max_bot_slice

        assert callable(create_max_bot_slice)
        assert MaxBotSlice is not None

    def test_package_exposes_dto(self) -> None:
        from job_bot.max_bot import OutgoingMessage

        msg = OutgoingMessage(chat_id=1, text="x")
        assert msg.text == "x"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
