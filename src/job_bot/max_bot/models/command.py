"""Command DTO for the MAX Bot slice (issue #60).

Mirrors :mod:`job_bot.telegram_bot.models.command` so the two slices
parse slash-commands the same way. Keeps the dispatch table
(command name -> constant) in one place for tests and docs.
"""

from __future__ import annotations

from dataclasses import dataclass


CMD_START = "start"
CMD_HELP = "help"
CMD_STATS = "stats"
CMD_STATUS = "status"
CMD_REVIEW = "review"
CMD_CANCEL = "cancel"

# Slash prefix used to recognise commands.
_COMMAND_PREFIX = "/"

# Command names without the leading slash. Kept in sync with
# ``Command.parse`` below.
_KNOWN_COMMANDS: tuple[str, ...] = (
    CMD_START,
    CMD_HELP,
    CMD_STATS,
    CMD_STATUS,
    CMD_REVIEW,
    CMD_CANCEL,
)


@dataclass(frozen=True)
class Command:
    """A parsed slash-command from a MAX Bot message.

    Attributes:
        name: canonical command name (one of ``CMD_*``).
        args: free-form argument string after the command name
            (whitespace-stripped). Empty when the user typed
            ``/name`` without any extra text.
    """

    name: str
    args: str

    @classmethod
    def parse(cls, text: str) -> Command | None:
        """Parse ``text`` into a :class:`Command`, or ``None`` if invalid.

        ``text`` must start with ``/`` and the first whitespace-
        separated token (minus the leading ``/``) must be one of
        :data:`_KNOWN_COMMANDS`. The remainder of the message becomes
        ``args``.
        """
        if not text or not text.startswith(_COMMAND_PREFIX):
            return None
        # Split on the first whitespace to get name + rest.
        stripped = text[len(_COMMAND_PREFIX) :].lstrip()
        if not stripped:
            return None
        head, _, tail = stripped.partition(" ")
        name = head.lower()
        if name not in _KNOWN_COMMANDS:
            return None
        return cls(name=name, args=tail.strip())
