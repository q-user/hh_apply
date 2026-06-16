"""EmailHandler -- employer email sending (issue #145).

In-slice VSA wrapper for the legacy
``ApplyToVacanciesUseCase._send_email`` / ``_maybe_send_email`` /
``_build_message_placeholders`` helpers. Prefers the ``EmailSenderPort``
when supplied; falls back to the legacy SMTP path (``smtp`` +
``config``).

SMTP failures are logged and swallowed so a broken SMTP server does
not break the apply loop; the underlying ``send`` method raises only
when both the port and the legacy path fail.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hh_applicant_tool.application.ports import EmailSenderPort

logger = logging.getLogger(__package__)


class EmailHandler:
    """In-slice email handler (issue #145).

    Args:
        email_sender: optional :class:`EmailSenderPort` (issue #36).
        smtp: legacy ``smtplib.SMTP`` client (fallback).
        config: legacy dict-like config providing ``smtp.from`` /
            ``smtp.user`` and the ``apply_mail_subject`` /
            ``apply_mail_body`` templates (fallback).
    """

    def __init__(
        self,
        email_sender: "EmailSenderPort | None" = None,
        *,
        smtp: Any = None,
        config: Any = None,
    ) -> None:
        self._email_sender = email_sender
        self._smtp = smtp
        self._config = config

    # ─── Public API ────────────────────────────────────────────

    def send(self, to: str, subject: str, body: str) -> None:
        """Send a single email.

        Prefers the :class:`EmailSenderPort` when supplied. On SMTP
        exception, falls through to the legacy SMTP path. Raises
        :class:`RuntimeError` only when both paths are unavailable.
        """
        if self._email_sender is not None:
            try:
                self._email_sender.send_email(to, subject, body)
                return
            except smtplib.SMTPException as ex:
                logger.warning("EmailSenderPort failed: %s", ex)

        if self._smtp is None or self._config is None:
            raise RuntimeError(
                "SMTP клиент или конфиг не настроены "
                "(send_email=True требует обоих)"
            )
        cfg = self._config.get("smtp", {})
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.get("from") or cfg.get("user")
        msg["To"] = to
        msg.set_content(body)
        self._smtp.send_message(msg)

    def maybe_send(
        self,
        vacancy: dict[str, Any],
        employer_id: str | None,
        placeholders: dict[str, Any],
        site_emails: dict[str, Any],
        command: Any,
    ) -> None:
        """If ``command.send_email`` is True, find the recipient and send.

        Recipient priority: ``vacancy.contacts.email`` → site-parsed
        ``site_emails[employer_id]``. SMTP failures are logged and
        swallowed (the apply loop must not crash on email errors).
        """
        if not getattr(command, "send_email", False):
            return
        mail_to: str | list[str] | None = (vacancy.get("contacts") or {}).get(
            "email"
        )
        if mail_to is None and employer_id is not None:
            mail_to = site_emails.get(employer_id)
        if not mail_to:
            return
        if isinstance(mail_to, list):
            mail_to = ", ".join(mail_to)
        mail_subject = self._rand_text(
            (self._config.get("apply_mail_subject") if self._config else None)
            or "{Отклик|Резюме} на вакансию %(vacancy_name)s"
        )
        mail_body = self._unescape_string(
            self._rand_text(
                (self._config.get("apply_mail_body") if self._config else None)
                or "{Здравствуйте|Добрый день}, "
                "{прошу рассмотреть|пожалуйста рассмотрите} "
                "мое резюме %(resume_url)s на вакансию %(vacancy_name)s."
                % placeholders
            )
        )
        try:
            self.send(mail_to, mail_subject, mail_body)
            logger.info(
                "[EMAIL] Отправлено письмо на email по поводу вакансии %s",
                vacancy.get("alternate_url"),
            )
        except smtplib.SMTPException as ex:
            logger.error(f"Ошибка отправки письма: {ex}")

    @staticmethod
    def build_message_placeholders(
        vacancy: dict[str, Any], placeholders: dict[str, Any]
    ) -> dict[str, Any]:
        """Build the placeholder dict used by the mail body template."""
        employer = vacancy.get("employer") or {}
        return {
            "vacancy_name": vacancy.get("name", ""),
            "employer_name": employer.get("name", ""),
            **placeholders,
        }

    # ─── Internals ─────────────────────────────────────────────

    @staticmethod
    def _rand_text(template: str) -> str:
        """Resolve ``{a|b}`` alternatives in ``template``.

        Local re-implementation of the legacy ``hh_applicant_tool.utils.text.rand_text``
        helper so the in-slice handler does not depend on the legacy package.
        """
        import random
        import re

        def _replace(match: "re.Match[str]") -> str:
            options = match.group(1).split("|")
            return random.choice(options) if options else match.group(0)

        return re.sub(r"\{([^{}]+)\}", _replace, template)

    @staticmethod
    def _unescape_string(text: str) -> str:
        """HTML-unescape a string.

        Local re-implementation of the legacy ``hh_applicant_tool.utils.text.unescape_string``
        helper so the in-slice handler does not depend on the legacy package.
        """
        import html

        return html.unescape(text)


__all__ = ["EmailHandler"]
