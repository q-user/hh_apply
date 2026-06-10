"""Event bus for cross-slice communication."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class Event:
    """Base event class."""

    event_type: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=lambda: __import__("time").time())


EventHandler = Callable[[Event], Any]


class EventBus:
    """Simple event bus for cross-slice communication."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe to an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribe from an event type."""
        if event_type in self._handlers:
            self._handlers[event_type].remove(handler)

    def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            handler(event)

    def publish_async(self, event: Event) -> None:
        """Publish an event asynchronously (fire and forget)."""
        # For now, just call publish. In future, could use asyncio.
        self.publish(event)


# Global event bus instance
_global_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    global _global_event_bus
    if _global_event_bus is None:
        _global_event_bus = EventBus()
    return _global_event_bus


def set_event_bus(bus: EventBus) -> None:
    """Set the global event bus instance (useful for testing)."""
    global _global_event_bus
    _global_event_bus = bus
