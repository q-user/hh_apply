"""Per-slice port definitions re-export shim (issue #158).

The legacy ``hh_applicant_tool.application.ports`` module held a mix
of port Protocols used by both the ``application_prep`` and
``application_submit`` use cases. After the VSA migration those
Protocols were either:

* promoted to a per-slice ``ports/`` module (e.g. ``captcha_port``,
  ``email_port``, ``apply_one_port``,
  ``application_submit/ports/``);
* kept in :mod:`job_bot.shared.ports` because they are cross-cutting
  (``AIClientPort``, ``CaptchaSolverPort``, ``RateLimiterPort``,
  ``Clock``, ``CancellationToken``, ``SiteParserPort``,
  ``EmailSenderPort``, ``HttpClientPort``, ``DelayPort``,
  ``TestVacancyLoggerPort``,
  ``VacancyDescriptionFetcherPort``).

This module exists only so the historical import path
``from job_bot.application_submit.ports.port_defs import X`` keeps
working for the duration of the VSA switchover. New code MUST import
from the per-slice or ``shared.ports`` location directly.
"""

from __future__ import annotations

from job_bot.application_submit.ports.captcha_port import CaptchaPort
from job_bot.application_submit.ports.email_port import EmailPort
from job_bot.shared.ports import (
    AIClientPort,
    CancellationToken,
    CaptchaSolverPort,
    Clock,
    DelayPort,
    EmailSenderPort,
    HttpClientPort,
    RateLimiterPort,
    SiteParserPort,
    TestVacancyLoggerPort,
    VacancyDescriptionFetcherPort,
)

__all__ = (
    "AIClientPort",
    "CancellationToken",
    "CaptchaPort",
    "CaptchaSolverPort",
    "Clock",
    "DelayPort",
    "EmailPort",
    "EmailSenderPort",
    "HttpClientPort",
    "RateLimiterPort",
    "SiteParserPort",
    "TestVacancyLoggerPort",
    "VacancyDescriptionFetcherPort",
)
