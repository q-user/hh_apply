"""Legacy ``hh_applicant_tool.api`` package — DEPRECATED (issue #152).

Historically this package exposed a flat namespace
(``hh_applicant_tool.api.datatypes.Resume``,
``hh_applicant_tool.api.ApiError``, …) re-exported from submodules.
Several legacy operations still rely on the old import shape; rather
than churn them all we keep the re-exports but mark the entire
package as deprecated. New code should import the VSA modules
directly (``job_bot.shared.api.datatypes``,
``job_bot.shared.api.errors``,
``job_bot.application_submit.errors``).
"""

from __future__ import annotations

import warnings

from .datatypes import (
    PaginatedItems,
    Resume,
)
from .errors import (
    ApiError,
    BadGateway,
    BadRequest,
    BadResponse,
    CaptchaRequired,
    ClientError,
    Forbidden,
    InternalServerError,
    LimitExceeded,
    Redirect,
    ResourceNotFound,
)

warnings.warn(
    "hh_applicant_tool.api is deprecated; "
    "use job_bot.shared.api and job_bot.application_submit.errors "
    "instead (issue #152).",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "ApiError",
    "BadGateway",
    "BadRequest",
    "BadResponse",
    "CaptchaRequired",
    "ClientError",
    "Forbidden",
    "InternalServerError",
    "LimitExceeded",
    "PaginatedItems",
    "Redirect",
    "ResourceNotFound",
    "Resume",
]
