"""Command value object + command-name constants.

Telegram commands are text updates that start with ``/``. The slice parses
them into a :class:`Command` for type-safe dispatch. Non-command text
(e.g. a regular message) returns ``None`` from :meth:`Command.parse`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

# Canonical command names (kept as constants for grep-ability).
CMD_START = "start"
CMD_HELP = "help"
CMD_STATS = "stats"
CMD_STATUS = "status"
CMD_REVIEW = "review"
CMD_CANCEL = "cancel"


@dataclass(frozen=True)
class Command:
    """A parsed Telegram command.

    Attributes:
        name: canonical command name (e.g. ``"start"``); always lower-case.
        args: positional arguments after the command (e.g. ``("42",)``).
        raw: the original text exactly as received.
    """

    name: str
    args: tuple[str, ...] = ()
    raw: str = ""

    @classmethod
    def parse(cls, text: str) -> Self | None:
        """Parse a raw text update into a :class:`Command`.

        Returns ``None`` for non-command text (anything that doesn't start
        with ``/``) and for malformed input (e.g. just ``"/"``).
        """
        if not text:
            return None
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None
        # Drop leading slash and split by whitespace.
        body = stripped[1:]
        if not body:
            return None
        parts = body.split()
        if not parts:
            return None
        name = parts[0].lower()
        args = tuple(parts[1:])
        return cls(name=name, args=args, raw=stripped)

    def __str__(self) -> str:
        if self.args:
            return f"/{self.name} {' '.join(self.args)}"
        return f"/{self.name}"
