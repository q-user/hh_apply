"""Legacy ``hh_applicant_tool.api.errors`` shim — DEPRECATED (issue #152).

The HH API client exception hierarchy has been split between the
shared kernel (:mod:`job_bot.shared.api.errors`, the generic classes)
and the :mod:`job_bot.application_submit.errors` slice (the
``CaptchaRequired`` / ``LimitExceeded`` subclasses).

This module is preserved as a deprecation shim that re-exports the
public surface so legacy call sites keep working for one release
window. New code should depend on the VSA locations directly.
"""

from __future__ import annotations

import warnings

from job_bot.application_submit.errors import CaptchaRequired, LimitExceeded
from job_bot.shared.api.errors import (
    ApiError,
    BadGateway,
    BadRequest,
    BadResponse,
    ClientError,
    Forbidden,
    InternalServerError,
    Redirect,
    ResourceNotFound,
)

warnings.warn(
    "hh_applicant_tool.api.errors is deprecated; "
    "use job_bot.shared.api.errors instead (issue #152).",
    DeprecationWarning,
    stacklevel=2,
)


__all__ = (
    "ApiError",
    "BadGateway",
    "BadRequest",
    "BadResponse",
    "CaptchaRequired",
    "ClientError",
    "Forbidden",
    "InternalServerError",
    "LimitExceeded",
    "Redirect",
    "ResourceNotFound",
)
