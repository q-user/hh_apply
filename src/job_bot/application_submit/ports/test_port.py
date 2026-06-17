"""TestPort -- interface for vacancy-test answer preparation and submission.

The slice re-uses :class:`hh_applicant_tool.services.vacancy_tests.VacancyTestsService`
for the heavy lifting; this protocol is the slice-local view of the
``fetch → prepare → build → submit`` pipeline.
"""

from __future__ import annotations

from typing import Any, Protocol

from job_bot.application_submit.models.test_answer import TestAnswer


class TestPort(Protocol):
    """Vacancy-test operations exposed by the slice."""

    def fetch_tests(self, response_url: str) -> Any:
        """Fetch the ``vacancyTests`` block from the response page."""
        ...

    def prepare_answers(self, test_data: Any) -> list[TestAnswer]:
        """Generate answers for the test (AI or rule-based)."""
        ...

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
        """Build the POST payload for ``/applicant/vacancy_response/popup``."""
        ...

    def submit_apply(
        self,
        response_url: str,
        payload: dict[str, Any],
        *,
        xsrf_token: str,
    ) -> dict[str, Any]:
        """POST the payload and return the JSON result."""
        ...


__all__ = ["TestPort"]
