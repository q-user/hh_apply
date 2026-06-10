"""TestHandler -- slice-level facade over :class:`VacancyTestsService`.

The heavy lifting (HTTP fetch, payload construction, POST) lives in
:mod:`hh_applicant_tool.services.vacancy_tests`. This handler adapts
the legacy service's return types to the slice's :class:`TestAnswer`
DTOs so the rest of the slice doesn't depend on the storage model.
"""

from __future__ import annotations

import logging
from typing import Any

from job_bot.application_submit.models.test_answer import TestAnswer

logger = logging.getLogger(__package__)


class TestHandler:
    """Vacancy-test answer preparation and submission."""

    def __init__(
        self,
        session: Any,
        ai_client: Any | None = None,
        *,
        delay: Any | None = None,
    ) -> None:
        from hh_applicant_tool.services.vacancy_tests import (
            VacancyTestsService,
        )

        self._session = session
        self._ai_client = ai_client
        # The legacy service is responsible for HTTP fetch / submit
        # and answer generation. We project the storage model answers
        # back to slice-local ``TestAnswer`` DTOs.
        self._service = VacancyTestsService(
            session=session, ai_client=ai_client, delay=delay
        )

    def fetch_tests(self, response_url: str) -> Any:
        """Fetch the ``vacancyTests`` block from the response page."""
        return self._service.fetch_tests(response_url)

    def prepare_answers(self, test_data: Any) -> list[TestAnswer]:
        """Generate answers for each task (AI or rule-based fallback)."""
        answers = self._service.prepare_answers(test_data)
        return [_project_answer(a) for a in answers]

    def build_payload(
        self,
        test_data: Any,
        answers: list[TestAnswer],
        *,
        vacancy_id: str | int,
        resume_hash: str,
        letter: str = "",
        xsrf_token: str,
    ) -> dict[str, Any]:
        """Build the POST payload for ``/applicant/vacancy_response/popup``.

        The legacy service expects ``ApplicationTestAnswerModel`` instances;
        we adapt on the fly so callers can work with :class:`TestAnswer`.
        """
        return self._service.build_apply_payload_from_answers(
            test_data=test_data,
            answers=[_to_legacy_model(a) for a in answers],
            vacancy_id=vacancy_id,
            resume_hash=resume_hash,
            letter=letter,
            xsrf_token=xsrf_token,
        )

    def submit_apply(
        self,
        response_url: str,
        payload: dict[str, Any],
        *,
        xsrf_token: str,
    ) -> dict[str, Any]:
        """POST the payload and return the JSON result."""
        return self._service.submit_apply(
            response_url, payload, xsrf_token=xsrf_token
        )


# ─── Helpers ───────────────────────────────────────────────────────────


def _project_answer(model: Any) -> TestAnswer:
    """Project a legacy ``ApplicationTestAnswerModel`` to a slice DTO."""
    return TestAnswer(
        task_id=str(getattr(model, "task_id", "")),
        question=getattr(model, "question", None),
        answer_type=getattr(model, "answer_type", None),
        options_json=getattr(model, "options_json", None),
        generated_answer=getattr(model, "generated_answer", None),
        selected_solution_id=getattr(model, "selected_solution_id", None),
        review_status=getattr(model, "review_status", "generated")
        or "generated",
    )


def _to_legacy_model(answer: TestAnswer) -> Any:
    """Build a minimal ``ApplicationTestAnswerModel`` for the legacy API."""
    from hh_applicant_tool.storage.models.application_test_answer import (
        ApplicationTestAnswerModel,
    )

    return ApplicationTestAnswerModel(
        draft_id=0,
        task_id=answer.task_id,
        question=answer.question,
        answer_type=answer.answer_type,
        options_json=answer.options_json,
        generated_answer=answer.generated_answer,
        selected_solution_id=answer.selected_solution_id,
        review_status=answer.review_status,
    )


__all__ = ["TestHandler"]
