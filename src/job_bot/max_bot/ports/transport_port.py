"""MaxTransportPort -- Protocol contract for the MAX transport layer.

The slice's transport handler depends on this Protocol; a real
implementation will be wired in later (HTTP client against
``https://max.ru/botapi/``). For now tests / callers inject a stub
that satisfies the same shape.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MaxTransportPort(Protocol):
    """Minimal transport interface used by the MaxBot slice.

    Mirrors the public surface of the Telegram transport port so the
    two slices stay symmetric and call-sites can be swapped cheaply.
    """

    def send_message(self, chat_id: int, text: str) -> bool:
        """Send a plain text message to ``chat_id``.

        Returns ``True`` on success, ``False`` otherwise.
        """
        ...

    def get_updates(
        self, offset: int | None = None, timeout: int = 30
    ) -> list[dict[str, Any]]:
        """Long-poll the MAX Bot API for new updates.

        Args:
            offset: identifier of the first update to return; the
                transport should return updates with ``update_id >= offset``.
            timeout: long-poll timeout in seconds.
        """
        ...
