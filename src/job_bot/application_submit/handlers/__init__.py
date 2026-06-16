"""application_submit slice handlers.

The 5 new per-phase handlers (SearchHandler, ScoreHandler,
CoverLetterHandler, SkipHandler, EmailHandler, CaptchaHandler)
are *not* eagerly imported here — they would form a circular
import cycle through ``hh_applicant_tool.api`` (legacy shim) →
``application_submit.errors`` → ``application_submit.handlers``
→ ``search_handler`` → ``vacancy_search`` → ``hh_applicant_tool.api``.

Consumers should import the 5 new handlers via their full module
path::

    from job_bot.application_submit.handlers.search_handler import SearchHandler
    from job_bot.application_submit.handlers.score_handler import ScoreHandler
    # ... etc

The 4 legacy handlers (apply_one, job, retry, test) stay eagerly
imported because they are in the hot path of the worker loop and
have no circular-import risk.
"""

from __future__ import annotations

from .apply_one_handler import ApplyOneHandler
from .job_handler import LOCK_TIMEOUT_SECONDS, JobHandler
from .retry_handler import DEFAULT_MAX_ATTEMPTS, RetryHandler
from .test_handler import TestHandler

__all__ = [
    "ApplyOneHandler",
    "JobHandler",
    "LOCK_TIMEOUT_SECONDS",
    "RetryHandler",
    "DEFAULT_MAX_ATTEMPTS",
    "TestHandler",
    # The 5 new per-phase handlers (issue #145) are imported lazily via
    # __getattr__ below; do not add them to __all__ eagerly.
    "CaptchaHandler",
    "CoverLetterHandler",
    "EmailHandler",
    "ScoreHandler",
    "SearchHandler",
    "SkipHandler",
]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """Lazy-import the 5 per-phase handlers (issue #145).

    Defers the import until first attribute access, which keeps the
    ``application_submit`` import graph acyclic when the legacy
    ``hh_applicant_tool.api`` shim is in the chain.
    """
    if name in (
        "SearchHandler",
        "ScoreHandler",
        "CoverLetterHandler",
        "SkipHandler",
        "EmailHandler",
        "CaptchaHandler",
    ):
        from importlib import import_module

        module_name = name.replace("Handler", "_handler").lower()
        module = import_module(
            f".handlers.{module_name}", package="job_bot.application_submit"
        )
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
