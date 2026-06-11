"""ApplyOneHandler -- thin VSA wrapper over ``make_default_apply_one``.

The actual HTTP submission and error classification are delegated to
:func:`hh_applicant_tool.services.apply_one.make_default_apply_one`
(no reimplementation). This handler just adapts the slice's DI
surface to the legacy callable contract.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__package__)


class ApplyOneHandler:
    """Apply-one callable exposed by the slice as ``ApplyOnePort``."""

    def __init__(
        self,
        api_client: Any,
        *,
        session: Any | None = None,
        xsrf_token: str | None = None,
        ai_client: Any | None = None,
        convert_errors: bool = False,
    ) -> None:
        from hh_applicant_tool.services.apply_one import make_default_apply_one

        self._api_client = api_client
        self._session = session
        self._xsrf_token = xsrf_token
        self._ai_client = ai_client
        # The legacy factory returns a closure bound to the dependencies
        # we just captured. The closure raises ``RetryableError`` /
        # ``FatalError`` from ``apply_worker`` on failure, which is
        # exactly the contract this slice's port documents.
        #
        # ``convert_errors`` defaults to ``False`` in the slice path so
        # that :class:`CaptchaRequired` and :class:`LimitExceeded` are
        # propagated as-is (issue #73). Legacy callers (the
        # ``ApplyWorkerService``) still get the wrapped contract by
        # constructing ``make_default_apply_one`` directly with
        # ``convert_errors=True`` (the factory's default).
        self._apply_one = make_default_apply_one(
            api_client,
            session=session,
            xsrf_token=xsrf_token,
            ai_client=ai_client,
            convert_errors=convert_errors,
        )

    def __call__(self, draft: Any) -> None:
        """Apply a single draft to hh.ru.

        Success returns ``None``. Failure propagates one of:

        * :class:`RetryableError` / :class:`FatalError` from
          :mod:`hh_applicant_tool.services.apply_worker` (5xx, 400,
          network, …);
        * :class:`CaptchaRequired` / :class:`LimitExceeded` from
          :mod:`hh_applicant_tool.api.errors` (only when
          ``convert_errors=False``, which is the default for the slice
          path — see issue #73).

        The VSA :class:`ApplicationSubmitAdapter` relies on the
        captcha/limit exceptions to trigger the special
        captcha-resolution and loop-termination branches in
        :meth:`ApplyToVacanciesUseCase._send_apply_request`.
        """
        self._apply_one(draft)
        return None


__all__ = ["ApplyOneHandler"]
