"""Re-exports for backward compatibility — kept for legacy import paths.

Historically this package exposed a flat namespace
(``hh_applicant_tool.api.datatypes.Resume``,
``hh_applicant_tool.api.ApiError``, …) re-exported from submodules.
Several legacy operations still rely on the old import shape; rather
than churn them all we keep the re-exports but mark them as
deprecated by route: new code should import the typed submodules
directly (``hh_applicant_tool.api.errors``,
``hh_applicant_tool.api.datatypes``).
"""

from __future__ import annotations

from .datatypes import PaginatedItems, Resume
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
