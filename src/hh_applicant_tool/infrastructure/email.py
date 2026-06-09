"""Email infrastructure implementations."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__package__)


class SMTPEmailSender:
    """SMTP email sender implementation.

    Wraps smtplib behind the EmailSenderPort interface.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        user: str | None = None,
        password: str | None = None,
        use_ssl: bool = False,
        use_starttls: bool = True,
        from_addr: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Initialize SMTP email sender.

        Args:
            host: SMTP server host.
            port: SMTP server port.
            user: Optional username for authentication.
            password: Optional password for authentication.
            use_ssl: Use SSL/TLS from the start (SMTP_SSL).
            use_starttls: Use STARTTLS after connecting (for non-SSL).
            from_addr: Default sender address.
            timeout: Connection timeout in seconds.
        """
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._use_ssl = use_ssl
        self._use_starttls = use_starttls
        self._from_addr = from_addr
        self._timeout = timeout

    def send_email(self, to: str, subject: str, body: str) -> None:
        """Send an email.

        Args:
            to: Recipient email address.
            subject: Email subject.
            body: Email body text.
        """
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._from_addr or self._user
        msg["To"] = to
        msg.set_content(body)

        client_cls = smtplib.SMTP_SSL if self._use_ssl else smtplib.SMTP

        try:
            with client_cls(
                self._host, self._port, timeout=self._timeout
            ) as server:
                if not self._use_ssl and self._use_starttls:
                    server.starttls()

                if self._user and self._password:
                    server.login(self._user, self._password)

                server.send_message(msg)
                logger.debug("Email sent to %s", to)
        except smtplib.SMTPException as ex:
            logger.error("Failed to send email to %s: %s", to, ex)
            raise


class SMTPEmailSenderFromConfig:
    """Factory for creating SMTPEmailSender from config dict."""

    @staticmethod
    def create(config: dict[str, Any]) -> SMTPEmailSender:
        """Create sender from config dict.

        Expected config keys:
            host, port, user, password, ssl, starttls, from, timeout
        """
        return SMTPEmailSender(
            host=config["host"],
            port=config["port"],
            user=config.get("user"),
            password=config.get("password"),
            use_ssl=config.get("ssl", False),
            use_starttls=config.get("starttls", True),
            from_addr=config.get("from"),
            timeout=config.get("timeout", 30.0),
        )
