"""MAX Messenger Bot slice (issue #60).

Public surface:

* :class:`MaxBotSlice` and :func:`create_max_bot_slice` -- main
  entry point and factory.
* :class:`TransportHandler` -- long-polling loop.
* :class:`CommandHandler` -- slash-command dispatcher.
* :class:`MaxBotService` and :func:`create_max_bot_service` --
  orchestrator that wires the command handler.
* :class:`MaxTransportPort` -- the Protocol any transport must
  satisfy.
* :class:`RequestsMaxTransport` and :class:`MaxTransportError` --
  the real ``requests``-backed transport and its error type.
* :class:`InlineButton`, :class:`OutgoingMessage` -- outgoing
  message DTOs.
* :class:`Command` and the ``CMD_*`` constants -- parsed-command
  DTO.
"""

from job_bot.max_bot.handlers.command_handler import CommandHandler
from job_bot.max_bot.handlers.transport_handler import TransportHandler
from job_bot.max_bot.models.command import (
    CMD_CANCEL,
    CMD_HELP,
    CMD_REVIEW,
    CMD_START,
    CMD_STATS,
    CMD_STATUS,
    Command,
)
from job_bot.max_bot.models.message import InlineButton, OutgoingMessage
from job_bot.max_bot.ports.transport_port import MaxTransportPort
from job_bot.max_bot.requests_transport import (
    DEFAULT_API_URL,
    MaxTransportError,
    RequestsMaxTransport,
)
from job_bot.max_bot.services.bot_service import (
    MaxBotService,
    create_max_bot_service,
)
from job_bot.max_bot.slice import MaxBotSlice, create_max_bot_slice

__all__ = [
    # Slice
    "MaxBotSlice",
    "create_max_bot_slice",
    # Handlers
    "CommandHandler",
    "TransportHandler",
    # Models
    "CMD_CANCEL",
    "CMD_HELP",
    "CMD_REVIEW",
    "CMD_START",
    "CMD_STATS",
    "CMD_STATUS",
    "Command",
    "InlineButton",
    "OutgoingMessage",
    # Ports
    "MaxTransportPort",
    # Transports
    "DEFAULT_API_URL",
    "MaxTransportError",
    "RequestsMaxTransport",
    # Service
    "MaxBotService",
    "create_max_bot_service",
]
