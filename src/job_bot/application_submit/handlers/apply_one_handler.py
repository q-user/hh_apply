"""ApplyOneHandler -- VSA apply-one callable.

Encapsulates the actual HTTP submission to ``/negotiations`` plus
error classification (5xx/429/captcha/network → :class:`RetryableError`,
400/403/404 → :class:`FatalError`) and the vacancy-test pipeline
(:class:`VacancyTestsService` for ``has_test=True`` drafts).

This is the single source of truth for "send one application draft" in
the VSA world; the legacy ``hh_applicant_tool.services.apply_one`` shim
has been removed (issue #77).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from job_bot.application_submit.errors import FatalError, RetryableError

if TYPE_CHECKING:
    from hh_applicant_tool.storage.models.application_draft import (
        ApplicationDraftModel,
    )

logger = logging.getLogger(__package__)


def _get_xsrf_token(session: Any) -> str:
    """Extract the XSRF token from the hh.ru main page HTML."""
    marker = ',"xsrfToken":"'
    response = session.get("https://hh.ru/")
    start = response.text.find(marker)
    if start == -1:
        raise FatalError("xsrf token not found in session")
    start += len(marker)
    end = response.text.find('"', start)
    if end == -1:
        raise FatalError("malformed xsrf token")
    return response.text[start:end]


class ApplyOneHandler:
    """Apply-one callable exposed by the slice as :class:`ApplyOnePort`.

    Args:
        api_client: HTTP client for the HH API (with a ``.post`` method).
        session: ``requests.Session`` used for the test pipeline. If
            ``None``, falls back to ``api_client.session``.
        xsrf_token: XSRF token for test submissions. If ``None``, will
            be extracted from ``session`` on first use.
        ai_client: AI client for test answer generation (optional).
        convert_errors: if ``True`` (legacy default), wraps
            :class:`CaptchaRequired` and :class:`LimitExceeded` in
            :class:`RetryableError`. If ``False`` (VSA default), they
            propagate as-is so the surrounding
            :class:`ApplicationSubmitAdapter` can apply its captcha /
            limit branches (issue #73).
    """

    def __init__(
        self,
        api_client: Any,
        *,
        session: Any | None = None,
        xsrf_token: str | None = None,
        ai_client: Any | None = None,
        convert_errors: bool = False,
    ) -> None:
        self._api_client = api_client
        self._session = session
        self._xsrf_token = xsrf_token
        self._ai_client = ai_client
        self._convert_errors = convert_errors

    def __call__(self, draft: "ApplicationDraftModel") -> None:
        """Apply a single draft to hh.ru.

        Success returns ``None``. Failure raises one of:

        * :class:`RetryableError` (5xx, 429, network) — the worker
          should reschedule with backoff.
        * :class:`FatalError` (400/403/404) — the worker should give
          up on the job.
        * :class:`CaptchaRequired` / :class:`LimitExceeded` (only when
          ``convert_errors=False``) — propagated so the adapter can
          handle captcha resolution and stop-on-limit (issue #73).
        """
        if draft.has_test:
            self._apply_with_test(draft)
            return

        params = {
            "resume_id": draft.resume_id,
            "vacancy_id": str(draft.vacancy_id),
            "message": draft.cover_letter or "",
        }
        # Lazy imports to avoid a hard dependency on the legacy package
        # at module load time.
        from requests import RequestException

        from hh_applicant_tool.api.errors import (
            ApiError,
            CaptchaRequired,
            LimitExceeded,
        )

        try:
            response = self._api_client.post("/negotiations", params)
        except CaptchaRequired as ex:
            if not self._convert_errors:
                raise
            raise RetryableError(f"captcha required: {ex.captcha_url}") from ex
        except LimitExceeded as ex:
            if not self._convert_errors:
                raise
            raise RetryableError("hh limit exceeded") from ex
        except ApiError as ex:
            status = getattr(ex, "status_code", None)
            if status is not None and 500 <= status < 600:
                raise RetryableError(f"hh {status}: {ex.message}") from ex
            if status == 429:
                raise RetryableError("hh rate limited (429)") from ex
            raise FatalError(f"hh {status}: {ex.message}") from ex
        except RequestException as ex:
            raise RetryableError(f"network: {ex}") from ex
        except Exception as ex:  # noqa: BLE001
            raise RetryableError(f"unexpected: {ex!r}") from ex

        if response is None:
            # ``api_client.post`` may return ``None`` for redirect/dry-run.
            raise FatalError("empty response from /negotiations")

    # ─── Test pipeline (has_test=True) ───────────────────────────

    def _apply_with_test(self, draft: "ApplicationDraftModel") -> None:
        """Run the vacancy-test pipeline: fetch tests, generate answers,
        build payload, POST the application.

        Re-uses the existing :class:`VacancyTestsService` (the only
        place that knows the exact request shape for the
        ``/applicant/vacancy_response/popup`` endpoint).
        """
        from requests import RequestException

        from hh_applicant_tool.services.vacancy_tests import (
            VacancyTestsService,
        )

        session = self._session or getattr(self._api_client, "session", None)
        if session is None:
            raise FatalError("no HTTP session available for test application")

        token = self._xsrf_token or _get_xsrf_token(session)
        response_url = (
            f"https://hh.ru/applicant/vacancy_response"
            f"?vacancy_id={draft.vacancy_id}"
        )

        test_service = VacancyTestsService(
            session=session, ai_client=self._ai_client
        )

        try:
            test_data = test_service.fetch_tests(response_url)
        except ValueError as ex:
            raise FatalError(f"failed to fetch tests: {ex}") from ex

        try:
            answers = test_service.prepare_answers(test_data)
        except Exception as ex:  # noqa: BLE001
            raise FatalError(f"failed to prepare test answers: {ex}") from ex

        # ``resume_id`` doubles as ``resume_hash`` for the popup endpoint.
        payload = test_service.build_apply_payload_from_answers(
            test_data=test_data,
            answers=answers,
            vacancy_id=draft.vacancy_id,
            resume_hash=draft.resume_id,
            letter=draft.cover_letter or "",
            xsrf_token=token,
        )

        try:
            result = test_service.submit_apply(
                response_url, payload, xsrf_token=token
            )
        except RequestException as ex:
            raise RetryableError(
                f"network error submitting test apply: {ex}"
            ) from ex
        except Exception as ex:  # noqa: BLE001
            raise FatalError(f"failed to submit test apply: {ex}") from ex

        if not result.get("success", False):
            error_msg = (
                result.get("error") or result.get("message") or "unknown error"
            )
            raise FatalError(f"test apply failed: {error_msg}")


__all__ = ["ApplyOneHandler"]
