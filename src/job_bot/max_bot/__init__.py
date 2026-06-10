"""MAX Bot slice - MAX messenger integration.

Public API::

    from job_bot.max_bot import (
        MaxBotSlice,
        create_max_bot_slice,
        TransportHandler,
        OutgoingMessage,
        InlineButton,
        MaxTransportPort,
    )
"""

from __future__ import annotations

from job_bot.max_bot.handlers.transport_handler import TransportHandler
from job_bot.max_bot.models.message import InlineButton, OutgoingMessage
from job_bot.max_bot.ports.transport_port import MaxTransportPort
from job_bot.max_bot.slice import MaxBotSlice, create_max_bot_slice

__all__ = [
    # Slice
    "MaxBotSlice",
    "create_max_bot_slice",
    # Handlers
    "TransportHandler",
    # Models
    "InlineButton",
    "OutgoingMessage",
    # Ports
    "MaxTransportPort",
]
