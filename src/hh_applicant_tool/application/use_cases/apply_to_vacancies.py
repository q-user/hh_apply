"""Use case: отклик на вакансии (apply) -- thin VSA adapter.

Issue #145: this use case is a **thin adapter** over the VSA
:class:`job_bot.application_submit.slice.ApplicationSubmitSlice`. All
per-phase logic (search, score, cover letter, skip, email, captcha,
storage) lives in the in-slice handlers; the use case only:

* preserves the legacy public API (``__init__``, ``execute``,
  ``run_apply_pipeline``) for backward compatibility with
  ``tests/test_prepare_vacancies.py``,
  ``tests/test_use_case_with_ports.py``,
  ``tests/integration/test_telegram_channel_to_apply_flow.py``,
  and the ``AppContainer`` / ``operations/apply_vacancies.py`` /
  ``ui/api.py`` callers;
* constructs the slice from the constructor's ``api_client``,
  ``session``, ``storage``, AI clients, port objects, etc.;
* delegates :meth:`execute` and :meth:`run_apply_pipeline` to the
  slice.

The use case is no longer the orchestrator: the slice is. The 5
in-slice handlers are constructed lazily by the slice (``SearchHandler``,
``ScoreHandler``, ``CoverLetterHandler``, ``SkipHandler``,
``EmailHandler``, ``CaptchaHandler``) and exposed as ``slice.search``,
``slice.score``, ``slice.cover_letter``, ``slice.skip``,
``slice.email``, ``slice.captcha``.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from hh_applicant_tool.application.dto import (
        ApplyToVacanciesCommand,
        ApplyToVacanciesResult,
    )

logger = logging.getLogger(__package__)

ProgressCallback = Callable[[str], None]


class ApplyToVacanciesUseCase:
    """Thin adapter over the VSA :class:`ApplicationSubmitSlice` (issue #145).

    Public surface (preserved for backward compatibility):

    * ``__init__`` accepts the same arguments as before (issue #50
      Phase 2 ports + issue #89 slice wiring + issue #142 VSA handler
      DI + issue #147 service split). New ``application_submit_slice``
      parameter lets callers pre-inject a slice; otherwise a default
      one is built from the supplied deps.
    * :meth:`execute` runs the full pipeline and returns an
      :class:`ApplyToVacanciesResult`. Implementation: delegates to
      :meth:`ApplicationSubmitSlice.run_apply_pipeline`.
    * :meth:`run_apply_pipeline` is the VSA port entry point kept for
      backward compatibility with the ``LegacyUseCasePort`` tests
      and the ``test_use_case_with_ports.py`` regression suite. It
      also delegates to the slice.

    Attributes:
        api_client: ``api.client.ApiClient`` -- HTTP-клиент HH API.
        session: ``requests.Session`` -- низкоуровневая сессия
            (captcha, site parsing, ``hh.ru/vacancy/...`` raw HTML).
        storage: ``storage.StorageFacade`` -- legacy facade for
            ``skipped_vacancies`` / ``vacancies`` / ``contacts`` /
            ``employers`` / ``employer_sites`` (used by the slice's
            private helpers).
        cover_letter_ai, captcha_ai, xsrf_token, smtp, config: legacy
            deps passed through to the slice.
        vacancy_filter_ai, vacancy_filter_ai_factory: AI deps used by
            the slice's :class:`ScoreHandler` when ``command.ai_filter``
            is set.
        application_submit_slice: pre-injected VSA slice (issue #89);
            when ``None`` a default one is built.
    """

    def __init__(
        self,
        api_client: Any,
        session: Any,
        storage: Any,
        cover_letter_ai: Any,
        captcha_ai: Any,
        xsrf_token: str,
        *,
        vacancy_filter_ai: Any = None,
        vacancy_filter_ai_factory: Callable[[str], Any] | None = None,
        smtp: Any = None,
        config: Any = None,
        # Phase 2 ports (optional, backward compatible).
        captcha_solver: Any = None,
        site_parser: Any = None,
        email_sender: Any = None,
        cancellation: Any | None = None,
        clock: Any | None = None,
        test_logger: Any | None = None,
        # VSA wiring (issues #89, #142, #145).
        vacancy_search_service_factory: Any = None,
        application_submit_adapter: Any = None,
        application_submit_slice: Any = None,
        relevance_handler: Any = None,
        cover_letter_handler: Any = None,
        vacancy_search_handler: Any = None,
        database: Any = None,
    ) -> None:
        self.api_client = api_client
        self.session = session
        self.storage = storage
        self.cover_letter_ai = cover_letter_ai
        self.captcha_ai = captcha_ai
        self.xsrf_token = xsrf_token
        self.vacancy_filter_ai = vacancy_filter_ai
        self.vacancy_filter_ai_factory = vacancy_filter_ai_factory
        self.smtp = smtp
        self.config = config
        self._captcha_solver = captcha_solver
        self._site_parser = site_parser
        self._email_sender = email_sender
        self._cancellation = cancellation
        self._clock = clock
        self._test_logger = test_logger
        self._injected_vacancy_search_service_factory = (
            vacancy_search_service_factory
        )
        self._application_submit_adapter = application_submit_adapter

        # State populated in execute() / run_apply_pipeline().
        self.command: "ApplyToVacanciesCommand | None" = None
        self.cancel_event: threading.Event | None = None
        self.progress_callback: ProgressCallback | None = None

        # Build the VSA slice (issue #145). When the caller pre-injects
        # one, we use it verbatim; otherwise we build a default from
        # the constructor deps. The slice is the single VSA entry
        # point; the use case is a thin adapter.
        self._application_submit_slice: Any = (
            application_submit_slice
            or self._build_default_slice(
                relevance_handler=relevance_handler,
                cover_letter_handler=cover_letter_handler,
                database=database,
            )
        )

    # ─── Public API ────────────────────────────────────────────

    def execute(
        self,
        command: "ApplyToVacanciesCommand",
        *,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> "ApplyToVacanciesResult":
        """Run the apply pipeline (issue #145: delegates to the slice)."""
        self.command = command
        self.cancel_event = cancel_event
        self.progress_callback = progress_callback
        return self._application_submit_slice.run_apply_pipeline(
            command=command,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )

    def run_apply_pipeline(
        self,
        *,
        command: "ApplyToVacanciesCommand",
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> "ApplyToVacanciesResult":
        """VSA-port entry point -- delegates to the slice.

        Kept for backward compatibility with
        ``tests/test_use_case_with_ports.py`` (issue #89 partial
        bridge). The slice's :meth:`run_apply_pipeline` does the
        actual work; the use case is a pass-through.
        """
        return self.execute(
            command,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )

    # ─── Internals ─────────────────────────────────────────────

    def _build_default_slice(
        self,
        *,
        relevance_handler: Any,
        cover_letter_handler: Any,
        database: Any,
    ) -> Any:
        """Construct the default VSA :class:`ApplicationSubmitSlice`.

        When ``cover_letter_handler`` is ``None`` (the prep-phase
        handler was not pre-injected) we lazily build a temp-file
        ``Database`` and let the slice's :class:`CoverLetterHandler`
        use the prep-phase default construction. This mirrors the
        legacy ``_build_vsa_handlers`` behaviour (issue #142) and
        keeps the legacy public surface working without requiring
        the caller to wire the prep-phase handlers.
        """
        from job_bot.application_submit import (
            ApplicationSubmitSlice,
            CoverLetterHandler,
        )

        prep_cover_letter = cover_letter_handler
        if prep_cover_letter is None:
            from job_bot.application_prep.handlers.cover_letter_handler import (
                CoverLetterHandler as PrepCoverLetterHandler,
            )

            db = database
            if db is None:
                import tempfile

                tmp_db = tempfile.NamedTemporaryFile(
                    prefix="apply_vacancies_vsa_", suffix=".db", delete=False
                )
                tmp_db.close()
                from job_bot.shared.storage.database import Database

                db = Database(tmp_db.name)
            prep_cover_letter = PrepCoverLetterHandler(
                database=db,
                api_client=self.api_client,
                ai_client=self.cover_letter_ai,
            )

        # Convert the prep-phase handler into the submit-phase
        # adapter (the slice's ``CoverLetterHandler`` wraps the
        # prep's ``generate_cover_letter``). ``CoverLetterHandler``
        # here is the in-slice adapter from
        # ``job_bot.application_submit.handlers``.
        submit_cover_letter = CoverLetterHandler(prep_cover_letter)

        # The use case doesn't own a :class:`StorageFacade` of its
        # own -- it has a raw ``storage`` object passed in by the
        # caller. The slice's :class:`SkipHandler` uses the storage's
        # ``skipped_vacancies`` repo; the slice's private helpers
        # (``_save_vacancy_to_storage``, ``_load_employer_profile``)
        # use the same ``storage``. We forward the caller's
        # ``storage`` directly so the legacy side keeps working.
        return ApplicationSubmitSlice(
            storage_conn=self._resolve_storage_conn(),
            api_client=self.api_client,
            session=self.session,
            xsrf_token=self.xsrf_token,
            ai_client=self.cover_letter_ai,
            notifier=None,
            clock=self._clock,
            relevance_handler=relevance_handler,
            cover_letter_prep_handler=submit_cover_letter,
            storage=self.storage,
            smtp=self.smtp,
            config=self.config,
            captcha_solver=self._captcha_solver,
            email_sender=self._email_sender,
            captcha_ai=self.captcha_ai,
            vacancy_filter_ai=self.vacancy_filter_ai,
            vacancy_filter_ai_factory=self.vacancy_filter_ai_factory,
        )

    def _resolve_storage_conn(self) -> sqlite3.Connection:
        """Extract a raw ``sqlite3.Connection`` from the caller's ``storage``.

        The legacy use case receives a ``StorageFacade`` (which
        holds a long-lived ``sqlite3.Connection``); the VSA slice
        needs a raw connection. Falls back to a fresh in-memory
        connection when neither is available (used by some unit
        tests).
        """
        storage = self.storage
        if storage is None:
            return sqlite3.connect(":memory:")
        # ``StorageFacade.conn`` is the legacy long-lived connection.
        conn = getattr(storage, "conn", None)
        if isinstance(conn, sqlite3.Connection):
            return conn
        # The VSA ``StorageFacade`` (15-repo) wraps a ``Database``;
        # open a connection to its on-disk path.
        from job_bot.shared.storage.database import Database

        if isinstance(storage.database, Database):
            return sqlite3.connect(storage.database.path)
        return sqlite3.connect(":memory:")


__all__ = ["ApplyToVacanciesUseCase"]
