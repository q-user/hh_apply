"""Domain errors raised by the Application Submit slice.

These were previously defined in
:mod:`hh_applicant_tool.services.apply_worker` and are now part of the
VSA contract. The legacy module re-exports them for backward
compatibility and emits a ``DeprecationWarning`` on import.

Issue #152 also moved the slice-specific HH API error subclasses
(:class:`CaptchaRequired`, :class:`LimitExceeded`) here from
:mod:`hh_applicant_tool.api.errors` so the apply slice owns the
"is this transient?" classification next to the data classes that
make the decision.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from job_bot.shared.api.errors import ClientError

__all__ = (
    "CaptchaRequired",
    "FatalError",
    "LimitExceeded",
    "RetryableError",
)


class RetryableError(Exception):
    """Ошибка, после которой задачу можно повторить позже (сеть, 5xx, капча)."""


class FatalError(Exception):
    """Ошибка, после которой повтор бессмыслен (400/403/404, баг)."""


class LimitExceeded(ClientError):
    """The HH API rejected a request because the caller exceeded a quota.

    Raised by :meth:`ApiError.raise_for_status` when the response has
    status 400 and ``data['errors']`` contains an entry with
    ``value == "limit_exceeded"``. The apply worker converts it to a
    :class:`RetryableError` so the job is retried later.
    """


class CaptchaRequired(ClientError):
    """The HH API requires the user to solve a captcha before continuing.

    Raised by :meth:`ApiError.raise_for_status` when the response has
    status 403 and ``data['errors']`` contains an entry with
    ``value == "captcha_required"``. The apply worker converts it to a
    :class:`RetryableError` and the captcha infrastructure (issue #151)
    uses :attr:`captcha_url` to solve it.
    """

    @cached_property
    def captcha_url(self) -> str:
        errors: list[dict[str, Any]] = self._data.get("errors", [])
        captcha_match: dict[str, Any] = next(
            filter(
                lambda v: v.get("value") == "captcha_required",
                errors,
            ),
            {},
        )
        url = captcha_match.get("captcha_url")
        return str(url) if url is not None else ""

    @property
    def message(self) -> str:
        return f"Captcha required: {self.captcha_url}"
