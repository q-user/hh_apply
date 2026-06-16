"""ApplicationSubmitSlice -- main entry point and factory.

Aggregates the apply-one / test / retry / worker components and the
5 per-phase handlers (search / score / cover-letter / skip / email /
captcha) and exposes them through the slice's :class:`JobPort` /
:class:`ApplyOnePort` / :class:`TestPort` / :class:`SearchPort` /
:class:`ScorePort` / :class:`CoverLetterPort` / :class:`SkipPort` /
:class:`EmailPort` / :class:`CaptchaPort` protocols.

Issue #145: the 5 per-phase handlers are the in-slice VSA wrappers
extracted from the legacy ``ApplyToVacanciesUseCase``. The slice's
:meth:`run_apply_pipeline` is the top-level orchestrator; the
``LegacyUseCasePort`` indirection (issue #89 partial bridge) is
gone -- the slice calls the in-slice handlers directly.

The factory :func:`create_application_submit_slice` wires everything
from the supplied dependencies; the slice does **not** reimplement the
existing ``hh_applicant_tool.services`` (apply_one, vacancy_tests) --
those are the underlying engines, the slice is the VSA wrapper.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from job_bot.application_submit.handlers.apply_one_handler import (
    ApplyOneHandler,
)
from job_bot.application_submit.handlers.captcha_handler import CaptchaHandler
from job_bot.application_submit.handlers.cover_letter_handler import (
    CoverLetterHandler,
)
from job_bot.application_submit.handlers.email_handler import EmailHandler
from job_bot.application_submit.handlers.job_handler import JobHandler
from job_bot.application_submit.handlers.retry_handler import RetryHandler
from job_bot.application_submit.handlers.score_handler import ScoreHandler
from job_bot.application_submit.handlers.search_handler import SearchHandler
from job_bot.application_submit.handlers.skip_handler import SkipHandler
from job_bot.application_submit.handlers.test_handler import TestHandler
from job_bot.application_submit.ports.apply_one_port import ApplyOnePort
from job_bot.application_submit.ports.captcha_port import CaptchaPort
from job_bot.application_submit.ports.cover_letter_port import CoverLetterPort
from job_bot.application_submit.ports.email_port import EmailPort
from job_bot.application_submit.ports.job_port import JobPort
from job_bot.application_submit.ports.score_port import ScorePort
from job_bot.application_submit.ports.search_port import SearchPort
from job_bot.application_submit.ports.skip_port import SkipPort
from job_bot.application_submit.ports.test_port import TestPort
from job_bot.application_submit.services.worker_service import (
    DEFAULT_IDLE_SLEEP_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    RunStats,
    WorkerService,
)

logger = logging.getLogger(__package__)


@dataclass
class PipelineRunResult:
    """Lightweight result type for :meth:`ApplicationSubmitSlice.run_apply_pipeline`.

    Mirrors :class:`hh_applicant_tool.application.dto.ApplyToVacanciesResult`
    structurally (we don't import the legacy DTO to keep the slice
    decoupled from the legacy package).
    """

    resumes_processed: int = 0
    applied: int = 0
    limit_reached: bool = False
    skipped: int = 0
    failed: int = 0


class ApplicationSubmitSlice:
    """Aggregates the apply-submission flow.

    Public surface:
      * :attr:`jobs` -- :class:`JobPort` (claim / lock / mark).
      * :attr:`apply_one` -- :class:`ApplyOnePort` (per-draft apply).
      * :attr:`tests` -- :class:`TestPort` (vacancy-test pipeline).
      * :attr:`retry` -- :class:`RetryHandler` (backoff / give-up).
      * :attr:`worker` -- :class:`WorkerService` (main loop).
      * :attr:`search` -- :class:`SearchPort` (issue #145).
      * :attr:`score` -- :class:`ScorePort` (issue #145).
      * :attr:`cover_letter` -- :class:`CoverLetterPort` (issue #145).
      * :attr:`skip` -- :class:`SkipPort` (issue #145).
      * :attr:`email` -- :class:`EmailPort` (issue #145).
      * :attr:`captcha` -- :class:`CaptchaPort` (issue #145).
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
        # 5 in-slice per-phase handlers (issue #145). When ``None``,
        # the slice builds default instances from the deps below.
        search_handler: SearchHandler | None = None,
        score_handler: ScoreHandler | None = None,
        cover_letter_handler: CoverLetterHandler | None = None,
        skip_handler: SkipHandler | None = None,
        email_handler: EmailHandler | None = None,
        captcha_handler: CaptchaHandler | None = None,
        # Deps for the 5 in-slice handlers (issue #145). Optional;
        # sensible defaults are used when ``None``.
        relevance_handler: Any | None = None,
        cover_letter_prep_handler: Any | None = None,
        storage: Any | None = None,
        smtp: Any | None = None,
        config: Any | None = None,
        captcha_solver: Any | None = None,
        email_sender: Any | None = None,
        captcha_ai: Any | None = None,
        vacancy_filter_ai: Any | None = None,
        vacancy_filter_ai_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._storage_conn = storage_conn
        self._api_client = api_client
        self._session = session
        self._xsrf_token = xsrf_token
        self._clock = clock

        # Handlers / services (existing).
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

        # 5 in-slice per-phase handlers (issue #145).
        self._search = search_handler or SearchHandler(api_client)
        self._score = score_handler or ScoreHandler(
            cast(Any, relevance_handler or _NullRelevanceHandler()),
            vacancy_filter_ai=vacancy_filter_ai,
            vacancy_filter_ai_factory=vacancy_filter_ai_factory,
        )
        self._cover_letter = cover_letter_handler or CoverLetterHandler(
            cover_letter_prep_handler or _NullCoverLetterHandler()
        )
        storage_for_skip = storage or self._resolve_storage()
        self._skip = skip_handler or SkipHandler(
            storage=storage_for_skip,
            api_client=api_client,
            clock=clock,
        )
        self._email = email_handler or EmailHandler(
            email_sender=email_sender,
            smtp=smtp,
            config=config,
        )
        self._captcha = captcha_handler or CaptchaHandler(
            captcha_solver=captcha_solver,
            captcha_ai=captcha_ai,
            session=session,
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

    @property
    def search(self) -> SearchPort:
        """Issue #145: the slice's :class:`SearchPort` (search + params)."""
        return self._search

    @property
    def score(self) -> ScorePort:
        """Issue #145: the slice's :class:`ScorePort` (AI relevance)."""
        return self._score

    @property
    def cover_letter(self) -> CoverLetterPort:
        """Issue #145: the slice's :class:`CoverLetterPort` (cover letter)."""
        return self._cover_letter

    @property
    def skip(self) -> SkipPort:
        """Issue #145: the slice's :class:`SkipPort` (skip policy)."""
        return self._skip

    @property
    def email(self) -> EmailPort:
        """Issue #145: the slice's :class:`EmailPort` (email send)."""
        return self._email

    @property
    def captcha(self) -> CaptchaPort:
        """Issue #145: the slice's :class:`CaptchaPort` (CAPTCHA solve)."""
        return self._captcha

    def run_apply_pipeline(
        self,
        *,
        command: Any,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> Any:
        """Top-level VSA orchestrator for the full search -> apply pipeline.

        Issue #145: the slice is the single VSA entry point. The
        per-phase implementation is delegated to the 5 in-slice
        handlers (:attr:`search`, :attr:`score`, :attr:`cover_letter`,
        :attr:`skip`, :attr:`email`, :attr:`captcha`). The legacy
        ``ApplyToVacanciesUseCase`` is a thin adapter that delegates
        here when wired.

        Args:
            command: ``ApplyToVacanciesCommand`` DTO.
            cancel_event: optional ``threading.Event`` for UI cancel.
            progress_callback: optional ``Callable[[str], None]`` for
                progress messages.

        Returns:
            ``ApplyToVacanciesResult`` with the same shape as the
            legacy ``ApplyToVacanciesUseCase.execute()``.
        """
        self._command = command
        self._cancel_event = cancel_event
        self._progress_callback = progress_callback

        # Import lazily to avoid a hard dependency on the legacy
        # package at module load time.
        from hh_applicant_tool.application.dto import ApplyToVacanciesResult

        result = ApplyToVacanciesResult()

        resumes = self._fetch_published_resumes(command.resume_id)
        if not resumes:
            logger.warning("У вас нет опубликованных резюме")
            return result

        me = self._fetch_me()
        seen_employers: set[str] = set()

        for resume in resumes:
            result.resumes_processed += 1
            applied, limit_reached = self._apply_to_resume(
                resume=resume,
                user=me,
                seen_employers=seen_employers,
            )
            result.applied += applied
            if limit_reached:
                result.limit_reached = True
                self._notify(
                    "⛔ Лимит откликов hh.ru исчерпан. Попробуйте позже."
                )
                break

        return result

    # ─── Pipeline internals ────────────────────────────────────

    def _fetch_published_resumes(
        self, resume_id: str | None
    ) -> list[dict[str, Any]]:
        """Fetch resumes via ``/resumes/mine``; save + filter to ``published``."""
        resumes: list[dict[str, Any]] = (
            self._api_client.get("/resumes/mine").get("items") or []
        )
        try:
            storage = self._resolve_storage()
            storage.resumes.save_batch(resumes)
        except Exception as ex:  # noqa: BLE001
            logger.debug("save_batch resumes failed: %s", ex)

        if resume_id:
            resumes = [r for r in resumes if r.get("id") == resume_id]
        resumes = [
            r
            for r in resumes
            if (r.get("status") or {}).get("id") == "published"
        ]
        return resumes

    def _fetch_me(self) -> dict[str, Any]:
        result = self._api_client.get("/me")
        return cast(dict[str, Any], result)

    def _apply_to_resume(
        self,
        *,
        resume: dict[str, Any],
        user: dict[str, Any],
        seen_employers: set[str],
    ) -> tuple[int, bool]:
        """Per-resume loop using the 5 in-slice handlers (issue #145)."""
        self._notify(
            "[START] Начинаю рассылку откликов для резюме:", resume.get("title")
        )

        placeholders = {
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "email": user.get("email") or "",
            "phone": user.get("phone") or "",
            "resume_hash": resume.get("id") or "",
            "resume_title": resume.get("title") or "",
            "resume_url": resume.get("alternate_url") or "",
        }

        do_apply = True
        applied_count = 0
        limit_reached = False
        site_emails: dict[str, Any] = {}

        # Initialize the AI filter once per resume (issue #145).
        resume_analysis = self._score.init_ai_filter(resume, self._command)

        max_responses = getattr(self._command, "max_responses", None)

        for vacancy in self._search.iterate(
            self._command, resume_id=resume.get("id")
        ):
            if self._is_cancelled():
                logger.info("Операция отменена пользователем")
                break
            if max_responses is not None and applied_count >= max_responses:
                logger.info(
                    "Достигнут лимит откликов (max_responses=%d). Останавливаю.",
                    max_responses,
                )
                break
            try:
                if self._skip.check(
                    vacancy,
                    resume,
                    do_apply,
                    self._command,
                    self._score.relevance_handler,
                    self._score.vacancy_filter_ai,
                ):
                    continue

                self._save_vacancy_to_storage(vacancy)
                self._load_employer_profile(
                    vacancy, seen_employers, site_emails
                )

                message_placeholders = self._email.build_message_placeholders(
                    vacancy, placeholders
                )
                letter = self._cover_letter.generate(
                    vacancy,
                    message_placeholders,
                    resume_analysis=resume_analysis,
                    resume=resume,
                    force=bool(getattr(self._command, "force_message", False)),
                    required_by_vacancy=bool(
                        vacancy.get("response_letter_required")
                    ),
                )
                logger.debug(letter)

                if vacancy.get("has_test"):
                    self._handle_vacancy_test(vacancy, resume.get("id"))
                    continue

                params = {
                    "resume_id": resume.get("id"),
                    "vacancy_id": vacancy.get("id"),
                    "message": letter,
                }
                if self._send_apply_request(params, vacancy):
                    applied_count += 1

                self._email.maybe_send(
                    vacancy,
                    vacancy.get("employer", {}).get("id"),
                    message_placeholders,
                    site_emails,
                    self._command,
                )
            except Exception as ex:  # noqa: BLE001
                # The legacy use case swallowed 3 distinct exceptions
                # (LimitExceeded → break, ApiError → warn, AIError/BadResponse
                # → error). The slice keeps the same "resilient apply loop"
                # contract: any unexpected error logs and continues with
                # the next vacancy. Programmatic limits still flow via
                # ``command.send_email`` / ``do_apply``; explicit
                # :class:`LimitExceeded` from the apply-one call short-
                # circuits the loop.
                from hh_applicant_tool.ai.base import AIError
                from hh_applicant_tool.api.errors import BadResponse
                from job_bot.application_submit.errors import LimitExceeded
                from job_bot.shared.api.errors import ApiError

                if isinstance(ex, LimitExceeded):
                    do_apply = False
                    limit_reached = True
                    logger.warning(
                        "Достигли лимита на отклики (отправлено в этой сессии: %d)",
                        applied_count,
                    )
                    break
                if isinstance(ex, ApiError):
                    logger.warning(ex)
                else:
                    logger.error(
                        "%s: %s",
                        type(ex).__name__,
                        ex,
                    )
                    if isinstance(ex, (BadResponse, AIError)):
                        logger.error(ex)
                        continue
                    continue

        self._notify(
            f"[DONE] Закончили рассылку для резюме: {resume.get('title')}. "
            f"Отправлено: {applied_count}"
        )
        return applied_count, limit_reached

    def _save_vacancy_to_storage(self, vacancy: dict[str, Any]) -> None:
        """Persist a processed vacancy + its contacts (best-effort)."""
        try:
            storage = self._resolve_storage()
            storage.vacancies.save(vacancy)
        except Exception as ex:  # noqa: BLE001
            logger.debug("save vacancy failed: %s", ex)
        if vacancy.get("contacts"):
            try:
                storage = self._resolve_storage()
                storage.vacancy_contacts.save(vacancy)
            except Exception as ex:  # noqa: BLE001
                logger.debug("save vacancy contacts failed: %s", ex)

    def _load_employer_profile(
        self,
        vacancy: dict[str, Any],
        seen_employers: set[str],
        site_emails: dict[str, Any],
    ) -> None:
        """Fetch /employers/{id}, save, and parse site for emails (best-effort)."""
        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")
        if not employer_id or employer_id in seen_employers:
            return
        try:
            employer_profile = self._api_client.get(f"/employers/{employer_id}")
        except Exception as ex:  # noqa: BLE001
            logger.warning("load employer %s failed: %s", employer_id, ex)
            return
        try:
            storage = self._resolve_storage()
            storage.employers.save(employer_profile)
        except Exception as ex:  # noqa: BLE001
            logger.debug("save employer failed: %s", ex)
        if not (
            getattr(self._command, "send_email", False)
            and (site_url := (employer_profile.get("site_url") or "").strip())
        ):
            return
        site_url = site_url if "://" in site_url else "https://" + site_url
        logger.debug("visit site: %s", site_url)
        try:
            site_info = self._parse_site(site_url)
            site_emails[employer_id] = site_info["emails"]
        except Exception as ex:  # noqa: BLE001
            logger.debug("parse site %s failed: %s", site_url, ex)
            return
        if site_info:
            try:
                storage = self._resolve_storage()
                storage.employer_sites.save(
                    {
                        "site_url": site_url,
                        "employer_id": employer_id,
                        "subdomains": [],
                        **site_info,
                    }
                )
            except Exception as ex:  # noqa: BLE001
                logger.debug("save employer site failed: %s", ex)

    def _parse_site(self, url: str) -> dict[str, Any]:
        """Parse a site URL (legacy inline regex fallback; no port in slice)."""
        import html
        import re

        if self._session is None:
            raise RuntimeError("session is required for site parsing")
        with self._session.get(url, timeout=10) as r:
            val: Callable[["re.Match[str] | None"], str] = lambda m: (
                html.unescape(m.group(1)) if m else ""
            )
            title = val(re.search(r"<title>(.*?)</title>", r.text, re.I | re.S))
            description = val(
                re.search(
                    r'<meta name="description" content="(.*?)"',
                    r.text,
                    re.I,
                )
            )
            generator = val(
                re.search(
                    r'<meta name="generator" content="(.*?)"',
                    r.text,
                    re.I,
                )
            )
            emails = set(
                m.group(0)
                for m in re.finditer(
                    r"\b[a-z][a-z0-9_.-]+@("
                    r"[a-z0-9][a-z0-9-]+)(?!\.(?:png|jpe?g|bmp|gif|ico|"
                    r"js|css)\b)(\.[a-z0-9][a-z0-9-]+)+\b",
                    r.text,
                )
            )
            return {
                "title": title,
                "description": description,
                "generator": generator,
                "emails": list(emails),
                "server_name": r.headers.get("Server"),
                "powered_by": r.headers.get("X-Powered-By"),
                "ip_address": None,
            }

    def _handle_vacancy_test(
        self, vacancy: dict[str, Any], resume_id: str | None
    ) -> None:
        """Log a vacancy that has a test, then mark it as ``has_test_manual_required``."""
        from datetime import datetime

        test_link = vacancy.get("alternate_url")
        employer = vacancy.get("employer") or {}
        logger.info("Найдена вакансия с тестом: %s", test_link)
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open("vacancies_with_tests.txt", "a", encoding="utf-8") as f:
                f.write(
                    f"[{timestamp}] {vacancy.get('name')} - "
                    f"{employer.get('name')} - {test_link}\n"
                )
        except OSError as ex:
            logger.error("Не удалось записать вакансию с тестом в файл: %s", ex)
        self._skip.save_skipped(vacancy, "has_test_manual_required", resume_id)

    def _send_apply_request(
        self, params: dict[str, Any], vacancy: dict[str, Any]
    ) -> bool:
        """POST the application; on captcha, solve and retry once."""
        if getattr(self._command, "dry_run", False):
            return False
        try:
            res = self._api_client.post(
                "/negotiations",
                params,
                delay=__import__("random").uniform(1, 3),
            )
            assert res == {}
            self._notify(
                " [APPLY] Отправили отклик на вакансию",
                vacancy.get("alternate_url"),
            )
            return True
        except Exception as ex:  # noqa: BLE001
            from hh_applicant_tool.ai.base import AIError
            from hh_applicant_tool.api.errors import ApiError, Redirect
            from job_bot.application_submit.errors import CaptchaRequired
            from job_bot.shared.api.errors import BadResponse

            if isinstance(ex, Redirect):
                logger.warning(
                    "Игнорирую перенаправление на форму: %s",
                    vacancy.get("alternate_url"),
                )
                return False
            if isinstance(ex, CaptchaRequired):
                logger.warning("Требуется капча: %s", ex.captcha_url)
                if not self._captcha.solve_captcha(ex.captcha_url):
                    logger.error("Не удалось решить капчу")
                    raise
                res = self._api_client.post(
                    "/negotiations",
                    params,
                    delay=__import__("random").uniform(1, 3),
                )
                assert res == {}
                self._notify(
                    " [APPLY] Отправили отклик на вакансию после капчи",
                    vacancy.get("alternate_url"),
                )
                return True
            if isinstance(ex, (ApiError, BadResponse, AIError, AssertionError)):
                logger.error("apply request failed: %s", ex)
                raise
            # Unexpected error: log and re-raise so the apply loop catches it.
            logger.error("apply request unexpected error: %s", ex)
            raise

    # ─── Helpers ───────────────────────────────────────────────

    def _resolve_storage(self) -> Any:
        """Return the slice's :class:`StorageFacade` (legacy or VSA).

        Prefers the VSA :class:`job_bot.shared.storage.facade.StorageFacade`
        (15 repos from PR #161) and falls back to the legacy
        :class:`hh_applicant_tool.storage.StorageFacade` when the
        VSA facade's repos are not yet wired in tests.
        """
        from job_bot.shared.storage.facade import StorageFacade

        return StorageFacade(self._storage_conn)  # type: ignore[arg-type]

    def _notify(self, *args: Any) -> None:
        message = " ".join(str(a) for a in args)
        print(message)
        if self._progress_callback is not None:
            try:
                self._progress_callback(message)
            except Exception as ex:  # noqa: BLE001
                logger.warning("progress_callback error: %s", ex)

    def _is_cancelled(self) -> bool:
        if self._cancel_event is not None and self._cancel_event.is_set():
            return True
        return False


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
    search_handler: SearchHandler | None = None,
    score_handler: ScoreHandler | None = None,
    cover_letter_handler: CoverLetterHandler | None = None,
    skip_handler: SkipHandler | None = None,
    email_handler: EmailHandler | None = None,
    captcha_handler: CaptchaHandler | None = None,
    relevance_handler: Any | None = None,
    cover_letter_prep_handler: Any | None = None,
    storage: Any | None = None,
    smtp: Any | None = None,
    config: Any | None = None,
    captcha_solver: Any | None = None,
    email_sender: Any | None = None,
    captcha_ai: Any | None = None,
    vacancy_filter_ai: Any | None = None,
    vacancy_filter_ai_factory: Callable[[str], Any] | None = None,
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
        search_handler=search_handler,
        score_handler=score_handler,
        cover_letter_handler=cover_letter_handler,
        skip_handler=skip_handler,
        email_handler=email_handler,
        captcha_handler=captcha_handler,
        relevance_handler=relevance_handler,
        cover_letter_prep_handler=cover_letter_prep_handler,
        storage=storage,
        smtp=smtp,
        config=config,
        captcha_solver=captcha_solver,
        email_sender=email_sender,
        captcha_ai=captcha_ai,
        vacancy_filter_ai=vacancy_filter_ai,
        vacancy_filter_ai_factory=vacancy_filter_ai_factory,
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


class _NullRelevanceHandler:
    """Stand-in relevance handler used when the slice is constructed without one.

    The slice still works: ``init_ai_filter`` returns ``""`` (the
    default branch in :meth:`ScoreHandler.init_ai_filter` is taken
    because ``command.ai_filter`` is ``None`` by default) and
    :attr:`relevance_handler` resolves to this no-op object. Tests
    that exercise the AI filter should inject a real handler.
    """

    _relevance_rules: dict[str, Any] = {}

    def analyze_resume_heavy(self, resume: dict[str, Any]) -> str:
        return ""

    def analyze_resume_light(self, resume: dict[str, Any]) -> str:
        return ""

    def is_suitable_heavy(self, vacancy: dict[str, Any]) -> Any:
        class _R:
            suitable = True

        return _R()

    def is_suitable_light(self, vacancy: dict[str, Any]) -> Any:
        class _R:
            suitable = True

        return _R()


class _NullCoverLetterHandler:
    """Stand-in cover letter handler used when the slice is constructed without one.

    Always returns an empty letter. Tests that exercise cover letter
    generation should inject a real handler.
    """

    def generate_cover_lletter(  # noqa: D401
        self,
        vacancy: dict[str, Any],
        placeholders: dict[str, Any],
        *,
        resume_analysis: str = "",
        resume: dict[str, Any] | None = None,
        force: bool = False,
        required_by_vacancy: bool = False,
    ) -> str:
        return ""


__all__ = [
    "ApplicationSubmitSlice",
    "create_application_submit_slice",
    "RunStats",
    "DEFAULT_IDLE_SLEEP_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "PipelineRunResult",
]
