"""ApplyOnePort -- interface for the apply-one callable.

The slice re-uses :func:`hh_applicant_tool.services.apply_one.make_default_apply_one`
for the actual HTTP submission and error classification. The port is a
:class:`Protocol` with a single ``__call__`` so any callable satisfying
the contract (including ``make_default_apply_one``-wrapped closures
and test mocks) can be injected.
"""

from __future__ import annotations

from typing import Any, Protocol


class ApplyOnePort(Protocol):
    """Submit a single draft to hh.ru.

    Success returns ``None``; failure raises :class:`RetryableError` or
    :class:`FatalError` from :mod:`hh_applicant_tool.services.apply_worker`.
    """

    def __call__(self, draft: Any) -> None: ...


__all__ = ["ApplyOnePort"]
