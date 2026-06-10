"""ApplicationSubmitSlice -- main entry point and factory.

Aggregates the job / apply-one / test / retry / worker components and
exposes them through the slice's :class:`JobPort` / :class:`ApplyOnePort`
/ :class:`TestPort` protocols.

The factory :func:`create_application_submit_slice` wires everything
from the supplied dependencies; the slice does **not** reimplement the
existing ``hh_applicant_tool.services`` (apply_one, vacancy_tests) --
those are the underlying engines, the slice is the VSA wrapper.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any

from job_bot.application_submit.handlers.apply_one_handler import (
    ApplyOneHandler,
)
from job_bot.application_submit.handlers.job_handler import JobHandler
from job_bot.application_submit.handlers.retry_handler import RetryHandler
from job_bot.application_submit.handlers.test_handler import TestHandler
from job_bot.application_submit.ports.apply_one_port import ApplyOnePort
from job_bot.application_submit.ports.job_port import JobPort
from job_bot.application_submit.ports.test_port import TestPort
from job_bot.application_submit.services.worker_service import (
    DEFAULT_IDLE_SLEEP_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    RunStats,
    WorkerService,
)

logger = logging.getLogger(__package__)


class ApplicationSubmitSlice:
    """Aggregates the apply-submission flow.

    Public surface:
      * :attr:`jobs` -- :class:`JobPort` (claim / lock / mark).
      * :attr:`apply_one` -- :class:`ApplyOnePort` (per-draft apply).
      * :attr:`tests` -- :class:`TestPort` (vacancy-test pipeline).
      * :attr:`retry` -- :class:`RetryHandler` (backoff / give-up).
      * :attr:`worker` -- :class:`WorkerService` (main loop).
    """

    def __init__(
        self,
        storage_conn: sqlite3.Connection,
        api_client: Any,
        *,
        session: Any | None = None,
        xsrf_token: str | None = None,
        ai_client: Any | None = None,
        notifier: Callable[[str, str], None] | None = None,
        clock: Any | None = None,
        delay: Any | None = None,
        worker_id: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        idle_sleep_seconds: float = DEFAULT_IDLE_SLEEP_SECONDS,
    ) -> None:
        self._storage_conn = storage_conn
        self._api_client = api_client

        # Handlers / services
        self._jobs = JobHandler(storage_conn)
        self._apply_one_handler = ApplyOneHandler(
            api_client=api_client,
            session=session,
            xsrf_token=xsrf_token,
            ai_client=ai_client,
        )
        self._test_handler = TestHandler(
            session=session or _dummy_session(),
            ai_client=ai_client,
        )
        self._retry = RetryHandler()
        self._worker = WorkerService(
            storage_conn=storage_conn,
            apply_one=self._apply_one_handler,
            retry=self._retry,
            notifier=notifier,
            clock=clock,
            delay=delay,
            worker_id=worker_id,
            max_attempts=max_attempts,
            idle_sleep_seconds=idle_sleep_seconds,
        )

    # ─── Public surface ────────────────────────────────────────

    @property
    def storage_conn(self) -> sqlite3.Connection:
        """The raw ``sqlite3.Connection`` the slice operates on."""
        return self._storage_conn

    @property
    def api_client(self) -> Any:
        return self._api_client

    @property
    def jobs(self) -> JobPort:
        """The slice's :class:`JobPort` (claim / lock / mark)."""
        return self._jobs

    @property
    def apply_one(self) -> ApplyOnePort:
        """The slice's :class:`ApplyOnePort` (per-draft apply)."""
        return self._apply_one_handler

    @property
    def tests(self) -> TestPort:
        """The slice's :class:`TestPort` (vacancy-test pipeline)."""
        return self._test_handler

    @property
    def retry(self) -> RetryHandler:
        """Backoff / give-up policy used by the worker."""
        return self._retry

    @property
    def worker(self) -> WorkerService:
        """The :class:`WorkerService` orchestrator."""
        return self._worker

    @property
    def run_stats_class(self) -> type[RunStats]:
        """Convenience for callers that want to type-annotate stats."""
        return RunStats


def create_application_submit_slice(
    storage_conn: sqlite3.Connection,
    api_client: Any,
    *,
    session: Any | None = None,
    xsrf_token: str | None = None,
    ai_client: Any | None = None,
    notifier: Callable[[str, str], None] | None = None,
    clock: Any | None = None,
    delay: Any | None = None,
    worker_id: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    idle_sleep_seconds: float = DEFAULT_IDLE_SLEEP_SECONDS,
) -> ApplicationSubmitSlice:
    """Factory function to create an :class:`ApplicationSubmitSlice`."""
    return ApplicationSubmitSlice(
        storage_conn=storage_conn,
        api_client=api_client,
        session=session,
        xsrf_token=xsrf_token,
        ai_client=ai_client,
        notifier=notifier,
        clock=clock,
        delay=delay,
        worker_id=worker_id,
        max_attempts=max_attempts,
        idle_sleep_seconds=idle_sleep_seconds,
    )


def _dummy_session() -> Any:
    """Return a stand-in session for the test handler when none was given.

    The :class:`TestHandler` always needs a session; when the caller
    doesn't supply one we still create the handler with a no-op-ish
    MagicMock-like object so that the slice can be constructed without
    failing. The real session is only used when ``has_test=True``
    drafts are processed.
    """
    return _NullSession()


class _NullSession:
    """Minimal stand-in session that raises clearly when actually used."""

    def get(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "TestHandler used without a real session; pass session=... to "
            "create_application_submit_slice() when the worker processes "
            "drafts with has_test=True."
        )

    def post(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "TestHandler used without a real session; pass session=... to "
            "create_application_submit_slice() when the worker processes "
            "drafts with has_test=True."
        )


__all__ = [
    "ApplicationSubmitSlice",
    "create_application_submit_slice",
    "RunStats",
    "DEFAULT_IDLE_SLEEP_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
]
