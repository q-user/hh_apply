"""EmailPort -- interface for sending employer emails.

Implemented by :class:`job_bot.application_submit.handlers.email_handler.EmailHandler`.
The handler wraps the legacy ``_send_email`` / ``_maybe_send_email`` helpers
extracted from ``ApplyToVacanciesUseCase`` (issue #145).
"""

from __future__ import annotations

from typing import Any, Protocol


class EmailPort(Protocol):
    """Email sending (uses ``EmailSenderPort`` or legacy SMTP fallback)."""

    def send(self, to: str, subject: str, body: str) -> None:
        """Send a single email. Prefers the ``EmailSenderPort`` when
        supplied; falls back to the legacy ``smtp`` + ``config`` path.
        """
        ...

    def maybe_send(
        self,
        vacancy: dict[str, Any],
        employer_id: str | None,
        placeholders: dict[str, Any],
        site_emails: dict[str, Any],
        command: Any,
    ) -> None:
        """If ``command.send_email`` is True, find the recipient email
        for ``vacancy`` and call :meth:`send`.
        """
        ...

    @staticmethod
    def build_message_placeholders(
        vacancy: dict[str, Any], placeholders: dict[str, Any]
    ) -> dict[str, Any]:
        """Build the placeholder dict used by the mail body template."""
        ...


__all__ = ["EmailPort"]
