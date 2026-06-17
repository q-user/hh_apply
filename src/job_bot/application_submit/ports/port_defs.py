"""Per-slice port definitions re-export shim for application_submit (issue #179).

After the VSA migration in #158, cross-cutting port Protocols
(``AIClientPort``, ``CaptchaSolverPort``, ``RateLimiterPort``, ``Clock``,
``CancellationToken``, ``SiteParserPort``, ``EmailSenderPort``,
``HttpClientPort``, ``DelayPort``, ``TestVacancyLoggerPort``,
``VacancyDescriptionFetcherPort``) live in :mod:`job_bot.shared.ports`.
This module now re-exports ONLY the slice-specific ports
(:class:`CaptchaPort`, :class:`EmailPort`) for backwards compatibility
with the historical import path
``from job_bot.application_submit.ports.port_defs import X``.

New code MUST import slice-specific ports from their per-slice modules
(``job_bot.application_submit.ports.captcha_port`` / ``.email_port``)
and cross-cutting ports directly from :mod:`job_bot.shared.ports`.
"""

from __future__ import annotations

from job_bot.application_submit.ports.captcha_port import CaptchaPort
from job_bot.application_submit.ports.email_port import EmailPort

__all__ = ("CaptchaPort", "EmailPort")
