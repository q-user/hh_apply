"""Domain errors raised by the Application Submit slice.

These were previously defined in
:mod:`hh_applicant_tool.services.apply_worker` and are now part of the
VSA contract. The legacy module re-exports them for backward
compatibility and emits a ``DeprecationWarning`` on import.
"""

from __future__ import annotations


class RetryableError(Exception):
    """Ошибка, после которой задачу можно повторить позже (сеть, 5xx, капча)."""


class FatalError(Exception):
    """Ошибка, после которой повтор бессмыслен (400/403/404, баг)."""


__all__ = ["FatalError", "RetryableError"]
