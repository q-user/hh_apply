"""Telegram Bot slice - commands, digest, review flow.

Public API::

    from job_bot.telegram_bot import (
        TelegramBotSlice,
        create_telegram_bot_slice,
        BotService,
        CommandHandler,
        DigestHandler,
        ReviewHandler,
        TransportHandler,
        Command,
        OutgoingMessage,
        InlineButton,
        DigestOutcome,
    )
"""

from __future__ import annotations

from job_bot.telegram_bot.adapter import (
    TelegramBotAdapter,
    create_telegram_bot_adapter,
)
from job_bot.telegram_bot.handlers.command_handler import CommandHandler
from job_bot.telegram_bot.handlers.digest_handler import DigestHandler
from job_bot.telegram_bot.handlers.review_handler import ReviewHandler
from job_bot.telegram_bot.handlers.transport_handler import TransportHandler
from job_bot.telegram_bot.models.command import Command
from job_bot.telegram_bot.models.digest import DigestOutcome
from job_bot.telegram_bot.models.message import InlineButton, OutgoingMessage
from job_bot.telegram_bot.services.bot_service import BotService
from job_bot.telegram_bot.slice import (
    TelegramBotSlice,
    create_telegram_bot_slice,
)

__all__ = [
    # Slice
    "TelegramBotSlice",
    "create_telegram_bot_slice",
    # Service
    "BotService",
    # Adapter (operation-facing)
    "TelegramBotAdapter",
    "create_telegram_bot_adapter",
    # Handlers
    "CommandHandler",
    "DigestHandler",
    "ReviewHandler",
    "TransportHandler",
    # Models
    "Command",
    "OutgoingMessage",
    "InlineButton",
    "DigestOutcome",
]
