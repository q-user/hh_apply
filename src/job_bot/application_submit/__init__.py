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
]
