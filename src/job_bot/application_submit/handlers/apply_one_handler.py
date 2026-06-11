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
        self._apply_one = make_default_apply_one(
            api_client,
            session=session,
            xsrf_token=xsrf_token,
            ai_client=ai_client,
        )

    def __call__(self, draft: Any) -> None:
        """Apply a single draft to hh.ru.

        Success returns ``None``; failure propagates
        :class:`RetryableError` or :class:`FatalError` from
        :mod:`hh_applicant_tool.services.apply_worker`.
        """
        self._apply_one(draft)
        return None


__all__ = ["ApplyOneHandler"]
