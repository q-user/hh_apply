"""Tests for EmailHandler (issue #145).

The handler prefers the ``EmailSenderPort`` when supplied; falls back
to the legacy ``smtp`` + ``config`` SMTP path. The tests use a fake
``EmailSenderPort`` and a fake ``smtplib.SMTP`` client.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any
from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.application.dto import ApplyToVacanciesCommand
from job_bot.application_submit.handlers.email_handler import EmailHandler

# ─── send (EmailSenderPort) ───────────────────────────────────────────


class TestEmailHandlerSendPort:
    """When ``email_sender`` is supplied, the port is used."""

    def test_send_uses_port(self) -> None:
        sender = MagicMock()
        handler = EmailHandler(email_sender=sender)
        handler.send("a@b.com", "Subject", "Body")
        sender.send_email.assert_called_once_with("a@b.com", "Subject", "Body")

    def test_send_falls_through_on_smtp_exception(self) -> None:
        """If the port raises ``smtplib.SMTPException``, the handler
        falls through to the legacy SMTP path (which then raises
        ``RuntimeError`` when no ``smtp`` is configured)."""
        sender = MagicMock()
        sender.send_email.side_effect = smtplib.SMTPException("smtp down")
        handler = EmailHandler(email_sender=sender)
        with pytest.raises(RuntimeError, match="SMTP клиент или конфиг"):
            handler.send("a@b.com", "Subject", "Body")
        sender.send_email.assert_called_once()

    def test_send_falls_through_on_unexpected_exception(self) -> None:
        """If the port raises any non-``SMTPException`` error, the
        handler still tries the legacy SMTP path. (The port is
        user-provided; a port crash should not silently swallow
        the request.)"""
        sender = MagicMock()
        sender.send_email.side_effect = RuntimeError("port down")
        handler = EmailHandler(email_sender=sender)
        # No legacy SMTP configured → RuntimeError.
        with pytest.raises(RuntimeError, match="SMTP клиент или конфиг"):
            handler.send("a@b.com", "Subject", "Body")


# ─── send (legacy SMTP) ───────────────────────────────────────────────


class TestEmailHandlerSendLegacy:
    """When no port is supplied, the legacy ``smtp`` + ``config`` path
    is used."""

    def test_legacy_send_uses_smtp(self) -> None:
        smtp = MagicMock()
        config = {"smtp": {"from": "me@example.com", "user": "me"}}
        handler = EmailHandler(email_sender=None, smtp=smtp, config=config)
        handler.send("a@b.com", "Subject", "Body")
        smtp.send_message.assert_called_once()
        msg = smtp.send_message.call_args[0][0]
        assert isinstance(msg, EmailMessage)
        assert msg["Subject"] == "Subject"
        assert msg["From"] == "me@example.com"
        assert msg["To"] == "a@b.com"

    def test_legacy_raises_when_unconfigured(self) -> None:
        handler = EmailHandler(email_sender=None, smtp=None, config=None)
        with pytest.raises(RuntimeError, match="SMTP клиент или конфиг"):
            handler.send("a@b.com", "S", "B")


# ─── maybe_send ───────────────────────────────────────────────────────


class TestEmailHandlerMaybeSend:
    """``maybe_send`` looks up the recipient and calls :meth:`send`."""

    def test_maybe_send_no_command_send_email(self) -> None:
        """When ``command.send_email`` is False, ``maybe_send`` is a
        no-op (the port is never called)."""
        sender = MagicMock()
        handler = EmailHandler(email_sender=sender)
        command = ApplyToVacanciesCommand(send_email=False)
        handler.maybe_send(
            {
                "id": 1,
                "name": "V",
                "contacts": {"email": "hr@acme.example.com"},
            },
            42,
            {"first_name": "Иван"},
            {},
            command,
        )
        sender.send_email.assert_not_called()

    def test_maybe_send_uses_vacancy_contacts(self) -> None:
        """Recipient priority 1: ``vacancy.contacts.email``."""
        sender = MagicMock()
        handler = EmailHandler(email_sender=sender)
        command = ApplyToVacanciesCommand(send_email=True)
        handler.maybe_send(
            {
                "id": 1,
                "name": "Senior Python",
                "contacts": {"email": "hr@acme.example.com"},
                "alternate_url": "https://hh.ru/vacancy/1",
            },
            42,
            {"first_name": "Иван", "resume_url": "https://hh.ru/resume/r1"},
            {},
            command,
        )
        sender.send_email.assert_called_once()
        args = sender.send_email.call_args[0]
        assert args[0] == "hr@acme.example.com"
        assert "Senior Python" in args[1] or "резюме" in args[1].lower()

    def test_maybe_send_falls_back_to_site_emails(self) -> None:
        """Recipient priority 2: ``site_emails[employer_id]``."""
        sender = MagicMock()
        handler = EmailHandler(email_sender=sender)
        command = ApplyToVacanciesCommand(send_email=True)
        handler.maybe_send(
            {
                "id": 1,
                "name": "Senior Python",
                "alternate_url": "https://hh.ru/vacancy/1",
            },
            42,
            {"first_name": "Иван", "resume_url": "https://hh.ru/resume/r1"},
            {42: ["hr@acme.example.com"]},
            command,
        )
        sender.send_email.assert_called_once()
        assert sender.send_email.call_args[0][0] == "hr@acme.example.com"

    def test_maybe_send_no_recipient_is_noop(self) -> None:
        """When neither the vacancy's contacts nor the site-parsed
        emails yield a recipient, ``maybe_send`` is a no-op."""
        sender = MagicMock()
        handler = EmailHandler(email_sender=sender)
        command = ApplyToVacanciesCommand(send_email=True)
        handler.maybe_send(
            {"id": 1, "name": "V"},
            42,
            {},
            {},
            command,
        )
        sender.send_email.assert_not_called()

    def test_maybe_send_joins_list_recipients(self) -> None:
        """``mail_to`` may be a list of recipients (e.g. multiple
        emails from a single site); the handler joins them with
        ``", "``."""
        sender = MagicMock()
        handler = EmailHandler(email_sender=sender)
        command = ApplyToVacanciesCommand(send_email=True)
        handler.maybe_send(
            {
                "id": 1,
                "name": "V",
                "contacts": {"email": ["a@x.com", "b@x.com"]},
            },
            42,
            {},
            {},
            command,
        )
        sender.send_email.assert_called_once()
        assert sender.send_email.call_args[0][0] == "a@x.com, b@x.com"


# ─── build_message_placeholders ──────────────────────────────────────


class TestBuildMessagePlaceholders:
    """The placeholder dict has ``vacancy_name`` + ``employer_name``
    prepended to the existing placeholders."""

    def test_prepends_vacancy_and_employer(self) -> None:
        out = EmailHandler.build_message_placeholders(
            {"id": 1, "name": "Backend", "employer": {"name": "Acme"}},
            {"first_name": "Иван", "resume_url": "https://hh.ru/resume/r1"},
        )
        assert out["vacancy_name"] == "Backend"
        assert out["employer_name"] == "Acme"
        assert out["first_name"] == "Иван"
        assert out["resume_url"] == "https://hh.ru/resume/r1"

    def test_missing_fields_default_to_empty_string(self) -> None:
        out = EmailHandler.build_message_placeholders({"id": 1}, {})
        assert out["vacancy_name"] == ""
        assert out["employer_name"] == ""

    def test_missing_employer_defaults_to_empty_string(self) -> None:
        out = EmailHandler.build_message_placeholders(
            {"id": 1, "name": "V"}, {}
        )
        assert out["employer_name"] == ""


# ─── Protocol satisfaction ────────────────────────────────────────────


def test_email_handler_satisfies_email_port() -> None:
    from job_bot.application_submit.ports.email_port import EmailPort

    handler: EmailPort = EmailHandler(email_sender=MagicMock())
    assert callable(handler.send)
    assert callable(handler.maybe_send)
    assert callable(handler.build_message_placeholders)
