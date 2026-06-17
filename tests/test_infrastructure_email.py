"""Тесты инфраструктурной SMTP-отправки писем."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from job_bot.application_submit.services.email_sender import (
    SMTPEmailSender,
    SMTPEmailSenderFromConfig,
)

# ─── SMTPEmailSender ────────────────────────────────────────────


def _build_smtp_mock():
    """Создаёт MagicMock для smtplib.SMTP(SMTP_SSL) с context manager."""
    smtp = MagicMock(name="smtp")
    smtp.__enter__.return_value = smtp
    smtp.__exit__.return_value = False
    return smtp


def test_smtp_uses_smtp_ssl_when_use_ssl_true():
    """При use_ssl=True используется smtplib.SMTP_SSL."""
    smtp = _build_smtp_mock()
    with (
        patch("smtplib.SMTP_SSL", return_value=smtp) as ssl_cls,
        patch("smtplib.SMTP") as plain_cls,
    ):
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=465,
            use_ssl=True,
        )
        sender.send_email("user@example.com", "Subject", "Body")

    ssl_cls.assert_called_once_with("smtp.example.com", 465, timeout=30.0)
    plain_cls.assert_not_called()
    smtp.login.assert_not_called()
    smtp.starttls.assert_not_called()
    smtp.send_message.assert_called_once()
    # Subject/To/From в отправленном сообщении
    sent = smtp.send_message.call_args[0][0]
    assert sent["Subject"] == "Subject"
    assert sent["To"] == "user@example.com"


def test_smtp_uses_plain_smtp_with_starttls_by_default():
    """По умолчанию use_ssl=False, use_starttls=True — STARTTLS применяется."""
    smtp = _build_smtp_mock()
    with (
        patch("smtplib.SMTP", return_value=smtp) as plain_cls,
        patch("smtplib.SMTP_SSL") as ssl_cls,
    ):
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=587,
            use_ssl=False,
            use_starttls=True,
        )
        sender.send_email("user@example.com", "Subj", "Body")

    plain_cls.assert_called_once()
    ssl_cls.assert_not_called()
    smtp.starttls.assert_called_once()
    smtp.send_message.assert_called_once()


def test_smtp_skips_starttls_when_disabled():
    """use_starttls=False — STARTTLS не вызывается."""
    smtp = _build_smtp_mock()
    with patch("smtplib.SMTP", return_value=smtp):
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=25,
            use_ssl=False,
            use_starttls=False,
        )
        sender.send_email("user@example.com", "Subj", "Body")
    smtp.starttls.assert_not_called()
    smtp.send_message.assert_called_once()


def test_smtp_login_with_credentials():
    """При user/password вызывается login()."""
    smtp = _build_smtp_mock()
    with patch("smtplib.SMTP", return_value=smtp):
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=587,
            user="me@example.com",
            password="secret",
        )
        sender.send_email("x@example.com", "S", "B")
    smtp.login.assert_called_once_with("me@example.com", "secret")


def test_smtp_login_skipped_when_no_credentials():
    """Без user/password — login() не вызывается."""
    smtp = _build_smtp_mock()
    with patch("smtplib.SMTP", return_value=smtp):
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=25,
            use_ssl=False,
            use_starttls=False,
        )
        sender.send_email("x@example.com", "S", "B")
    smtp.login.assert_not_called()


def test_smtp_uses_from_addr_for_from_header():
    """from_addr переопределяет user в заголовке From."""
    smtp = _build_smtp_mock()
    with patch("smtplib.SMTP", return_value=smtp):
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=25,
            use_ssl=False,
            use_starttls=False,
            user="login@example.com",
            from_addr="custom@example.com",
        )
        sender.send_email("x@example.com", "S", "B")
    sent: EmailMessage = smtp.send_message.call_args[0][0]
    assert sent["From"] == "custom@example.com"


def test_smtp_falls_back_to_user_for_from_header():
    """Без from_addr — From равен user."""
    smtp = _build_smtp_mock()
    with patch("smtplib.SMTP", return_value=smtp):
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=25,
            use_ssl=False,
            use_starttls=False,
            user="login@example.com",
        )
        sender.send_email("x@example.com", "S", "B")
    sent: EmailMessage = smtp.send_message.call_args[0][0]
    assert sent["From"] == "login@example.com"


def test_smtp_message_has_text_body():
    """Body попадает в payload сообщения."""
    smtp = _build_smtp_mock()
    with patch("smtplib.SMTP", return_value=smtp):
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=25,
            use_ssl=False,
            use_starttls=False,
        )
        sender.send_email("x@example.com", "S", "Hello, world!")
    sent: EmailMessage = smtp.send_message.call_args[0][0]
    # Проверяем, что тело сообщения содержит наш текст
    payload = str(sent)
    assert "Hello, world!" in payload


def test_smtp_raises_on_smtp_exception():
    """smtplib.SMTPException пробрасывается наружу."""
    smtp = _build_smtp_mock()
    smtp.__enter__.side_effect = smtplib.SMTPException("connect failed")
    with patch("smtplib.SMTP", return_value=smtp):
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=25,
            use_ssl=False,
            use_starttls=False,
        )
        with pytest.raises(smtplib.SMTPException):
            sender.send_email("x@example.com", "S", "B")


def test_smtp_respects_timeout_argument():
    """Переданный timeout пробрасывается в smtplib.SMTP(...)."""
    smtp = _build_smtp_mock()
    with patch("smtplib.SMTP", return_value=smtp) as plain_cls:
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=25,
            use_ssl=False,
            use_starttls=False,
            timeout=7.5,
        )
        sender.send_email("x@example.com", "S", "B")
    # timeout передан в конструктор SMTP как kwarg
    assert plain_cls.call_args.kwargs.get("timeout") == 7.5


# ─── SMTPEmailSenderFromConfig ──────────────────────────────────


def test_smtp_from_config_required_keys():
    """create() читает host/port из конфига."""
    cfg = {
        "host": "smtp.example.com",
        "port": 587,
    }
    sender = SMTPEmailSenderFromConfig.create(cfg)
    assert sender._host == "smtp.example.com"
    assert sender._port == 587


def test_smtp_from_config_optional_keys():
    """create() подхватывает user/password/ssl/starttls/from/timeout."""
    cfg = {
        "host": "smtp.example.com",
        "port": 587,
        "user": "u@example.com",
        "password": "p",
        "ssl": True,
        "starttls": False,
        "from": "noreply@example.com",
        "timeout": 5.0,
    }
    sender = SMTPEmailSenderFromConfig.create(cfg)
    assert sender._user == "u@example.com"
    assert sender._password == "p"
    assert sender._use_ssl is True
    assert sender._use_starttls is False
    assert sender._from_addr == "noreply@example.com"
    assert sender._timeout == 5.0


def test_smtp_from_config_defaults():
    """create() со только обязательными полями — дефолты разумные."""
    cfg = {"host": "smtp.example.com", "port": 25}
    sender = SMTPEmailSenderFromConfig.create(cfg)
    assert sender._user is None
    assert sender._password is None
    assert sender._use_ssl is False
    assert sender._use_starttls is True
    assert sender._from_addr is None
    assert sender._timeout == 30.0
