"""Application Submission slice -- apply-worker, vacancy tests.

Public API::

    from job_bot.application_submit import (
        ApplicationSubmitSlice,
        create_application_submit_slice,
        WorkerService,
        RunStats,
        JobHandler,
        ApplyOneHandler,
        TestHandler,
        RetryHandler,
        ApplyJob,
        ApplyJobStatus,
        SubmitResult,
        SubmitStatus,
        TestAnswer,
    )

    slice_ = create_application_submit_slice(
        storage_conn=conn, api_client=api_client
    )
    stats = slice_.worker.run(stop_when_idle=True)
"""

from __future__ import annotations

from job_bot.application_submit.errors import FatalError, RetryableError
from job_bot.application_submit.handlers import (
    DEFAULT_MAX_ATTEMPTS,
    LOCK_TIMEOUT_SECONDS,
    ApplyOneHandler,
    JobHandler,
    RetryHandler,
    TestHandler,
)
from job_bot.application_submit.models import (
    ApplyJob,
    ApplyJobStatus,
    SubmitResult,
    SubmitStatus,
    TestAnswer,
    TestAnswerType,
)
from job_bot.application_submit.ports import (
    ApplyOnePort,
    JobPort,
    TestPort,
)
from job_bot.application_submit.services import (
    DEFAULT_IDLE_SLEEP_SECONDS,
    RunStats,
    WorkerService,
)
from job_bot.application_submit.slice import (
    ApplicationSubmitSlice,
    create_application_submit_slice,
)

__all__ = [
    # Slice
    "ApplicationSubmitSlice",
    "create_application_submit_slice",
    # Services
    "WorkerService",
    "RunStats",
    "DEFAULT_IDLE_SLEEP_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    # Handlers
    "JobHandler",
    "ApplyOneHandler",
    "TestHandler",
    "RetryHandler",
    "LOCK_TIMEOUT_SECONDS",
    # The 5 new per-phase handlers (issue #145) are imported lazily via
    # __getattr__ below; do not add them to __all__ eagerly.
    "CaptchaHandler",
    "CoverLetterHandler",
    "EmailHandler",
    "ScoreHandler",
    "SearchHandler",
    "SkipHandler",
    # Models
    "ApplyJob",
    "ApplyJobStatus",
    "SubmitResult",
    "SubmitStatus",
    "TestAnswer",
    "TestAnswerType",
    # Ports
    "JobPort",
    "ApplyOnePort",
    "TestPort",
    # Errors
    "FatalError",
    "RetryableError",
]


_LAZY_HANDLER_MODULES: dict[str, str] = {
    "SearchHandler": "search_handler",
    "ScoreHandler": "score_handler",
    "CoverLetterHandler": "cover_letter_handler",
    "SkipHandler": "skip_handler",
    "EmailHandler": "email_handler",
    "CaptchaHandler": "captcha_handler",
}


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """Lazy-import the 5 per-phase handlers (issue #145).

    The legacy ``apply_to_vacancies`` use case still does
    ``from job_bot.application_submit import CoverLetterHandler``
    (and the slice's own ``__init__`` docstring lists the 5 new
    handlers in its public surface). Importing them eagerly would
    form a circular import cycle through ``hh_applicant_tool.api``
    (legacy shim) → ``application_submit.errors`` →
    ``application_submit.handlers`` → ``search_handler`` →
    ``vacancy_search`` → ``hh_applicant_tool.api``. Defer the
    import until first attribute access to keep the graph acyclic.
    """
    if name in _LAZY_HANDLER_MODULES:
        from importlib import import_module

        module = import_module(
            f".handlers.{_LAZY_HANDLER_MODULES[name]}",
            package="job_bot.application_submit",
        )
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
