"""``NotificationPort`` -- protocol for sending vacancy-link notifications (issue #61).

The channel-monitoring slice fans out detected vacancy links to a
notification sink (Telegram bot, MAX bot, webhook, etc.). To keep the
slice independent of any concrete transport we expose a small
``Protocol`` that mirrors the public surface of
:class:`job_bot.telegram_bot.ports.transport_port.TelegramTransportPort.send_message`.

Adapters (Telegram / MAX) wrap their own transport and forward calls
to this port. A no-op adapter is provided for tests and dry-runs.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from job_bot.channel_monitoring.models.vacancy_link import VacancyLink


@runtime_checkable
class NotificationPort(Protocol):
    """Minimal notification interface used by the monitor service.

    The contract is intentionally tiny: a single ``send`` method that
    delivers a vacancy link to a target chat. Implementations decide
    formatting (plain text, Markdown, embed) and transport.
    """

    def send(self, chat_id: int, link: VacancyLink) -> None:
        """Deliver a vacancy link notification to ``chat_id``.

        Implementations MUST NOT raise on transient network errors --
        they should log and swallow so the monitor loop stays alive.
        """
        ...


class NullNotificationPort:
    """No-op notification adapter (issue #61).

    Used by tests and by the CLI ``--dry-run`` flag. Stores the last
    delivered link in ``self.last_sent`` for assertions.
    """

    def __init__(self) -> None:
        self.last_sent: tuple[int, VacancyLink] | None = None
        self.sent: list[tuple[int, VacancyLink]] = []

    def send(self, chat_id: int, link: VacancyLink) -> None:
        """Record the delivery without doing I/O."""
        self.last_sent = (chat_id, link)
        self.sent.append((chat_id, link))


def create_null_notification_port() -> NullNotificationPort:
    """Factory for the no-op notification port."""
    return NullNotificationPort()
