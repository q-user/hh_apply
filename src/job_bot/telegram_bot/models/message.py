"""OutgoingMessage DTO -- what the slice wants the transport to send.

Kept structurally similar to ``job_bot.telegram_bot.services.review_service.OutgoingMessage``
so the slice can re-use the existing review/digest services without
re-wrapping the wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class InlineButton:
    """A single inline button.

    Attributes:
        text: human-readable label.
        callback_data: data sent back via ``callback_query``; or use :attr:`url`.
        url: if set, the button opens a URL (no callback).
    """

    text: str
    callback_data: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class OutgoingMessage:
    """A message the slice wants to deliver to a chat.

    Attributes:
        chat_id: target chat id.
        text: message text.
        reply_markup: 2D list of inline buttons (rows of buttons).
        parse_mode: optional Telegram parse mode (``"Markdown"``, ``"HTML"``).
    """

    chat_id: int
    text: str
    reply_markup: list[list[InlineButton]] = field(default_factory=list)
    parse_mode: str | None = None
