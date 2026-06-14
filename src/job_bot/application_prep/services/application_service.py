"""Application draft preparation pipeline.

.. versionchanged:: 2.0
   Moved from ``hh_applicant_tool.services.applications`` to
   ``job_bot.application_prep.services.application_service``
   as part of the VSA switchover (issue #77).

Extracted from ``operations/apply_vacancies.py`` (issue #3). The service
orchestrates:

1. AI vacancy filtering (:class:`RelevanceService`).
2. Cover letter generation (:class:`CoverLetterService`).
3. (Optional) Test answering (:class:`VacancyTestsService`).
4. Saving :class:`ApplicationDraftModel` with status ``"prepared"``
   (or ``"rejected"`` if AI filter rejected).

Used from the future ``prepare-vacancies`` (issue #5) and optionally
from ``apply-vacancies`` (after refactoring).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.application_draft import (
    ApplicationDraftModel,
)
from hh_applicant_tool.storage.models.search_profile import SearchProfileModel
from hh_applicant_tool.storage.repositories.errors import RepositoryError
from job_bot.application_prep.utils import analysis_to_dict

if TYPE_CHECKING:
    from hh_applicant_tool.services.relevance import RelevanceService

logger = logging.getLogger(__package__)


class ApplicationsService:
    """Prepare a single application draft ``(resume, vacancy) -> draft``.

    Attributes:
        storage: storage facade (for upsert in ``application_drafts``).
        relevance: AI filtering service (or ``None`` — skip filter).
        cover_letter: cover letter generation service (or ``None``).
        vacancy_tests: vacancy test service (or ``None`` — skip test
            answer generation).
    """

    def __init__(
        self,
        storage: StorageFacade,
        relevance: RelevanceService | None = None,
        cover_letter: Any | None = None,
        vacancy_tests: Any | None = None,
    ):
        self.storage = storage
        self.relevance = relevance
        self.cover_letter = cover_letter
        self.vacancy_tests = vacancy_tests

    def prepare_one(
        self,
        *,
        resume: dict[str, Any],
        vacancy: dict[str, Any],
        search_profile: SearchProfileModel | None = None,
        resume_analysis: str = "",
        ai_filter_mode: str | None = None,
        placeholders: dict[str, Any] | None = None,
        force_message: bool = False,
        response_url: str | None = None,
    ) -> ApplicationDraftModel | None:
        """Prepare (or update) an application draft.

        Returns:
        - ``ApplicationDraftModel`` with status ``"prepared"`` — if vacancy
          passed AI filter;
        - ``ApplicationDraftModel`` with status ``"rejected"`` — if
          AI filter rejected (only score/reason/relevance_reason
          filled, ``cover_letter`` empty);
        - ``None`` — if vacancy is not interesting (e.g.
          ``response_url`` absent and signal to skip).

        Args:
        - ``resume``: dict (datatypes.Resume);
        - ``vacancy``: dict (datatypes.SearchVacancy);
        - ``search_profile``: optional — for ``search_profile_id``;
        - ``resume_analysis``: resume analysis text (used in letter);
        - ``ai_filter_mode``: ``"heavy"`` / ``"light"`` / ``None``;
        - ``placeholders``: ``first_name``/``last_name``/``resume_title``
          etc. for letter template;
        - ``force_message``: always generate a letter;
        - ``response_url``: URL of test page (if vacancy has
          ``has_test``). If not passed and ``has_test=True`` — draft is
          marked ``test_status='manual_required'`` without generation.
        """
        resume_id = resume.get("id")
        vacancy_id = vacancy.get("id")
        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")

        # 1. AI filtering (if enabled)
        relevance_score: int | None = None
        relevance_reason: str | None = None
        analysis_json: dict | None = None
        status = "prepared"

        if self.relevance is not None and ai_filter_mode in ("heavy", "light"):
            if ai_filter_mode == "heavy":
                result = self.relevance.is_suitable_heavy(vacancy)
            else:
                result = self.relevance.is_suitable_light(vacancy)
            if not result.suitable:
                status = "rejected"
                relevance_score = result.score
                relevance_reason = result.reason
                analysis_json = _analysis_to_dict(result)
            else:
                relevance_score = result.score
                relevance_reason = result.reason
                analysis_json = _analysis_to_dict(result)

        # If vacancy rejected by AI — save rejected-draft and exit
        if status == "rejected":
            draft = ApplicationDraftModel(
                search_profile_id=(
                    search_profile.id if search_profile else None
                ),
                resume_id=str(resume_id) if resume_id else "",
                vacancy_id=int(vacancy_id) if vacancy_id else 0,
                employer_id=int(employer_id) if employer_id else None,
                status=status,
                relevance_score=relevance_score,
                relevance_reason=relevance_reason,
                analysis_json=analysis_json,
                full_vacancy_json=vacancy,
                cover_letter=None,
                cover_letter_status=None,
                has_test=bool(vacancy.get("has_test")),
                test_status=None,
            )
            self.storage.application_drafts.save(draft)
            return draft

        # 2. Cover letter generation
        cover_letter: str | None = None
        cover_letter_status: str | None = None
        if self.cover_letter is not None:
            try:
                cover_letter = self.cover_letter.generate(
                    vacancy,
                    placeholders or {},
                    resume_analysis=resume_analysis,
                    resume=resume,
                    force=force_message,
                    required_by_vacancy=bool(
                        vacancy.get("response_letter_required")
                    ),
                )
                cover_letter_status = "generated"
            except Exception as ex:  # noqa: BLE001
                logger.warning(
                    "Не удалось сгенерировать сопроводительное письмо: %s",
                    ex,
                )
                cover_letter_status = "failed"

        # 3. Vacancy tests (without HTTP submission)
        has_test = bool(vacancy.get("has_test"))
        test_status: str | None = None
        generated_answers: list | None = None
        if has_test and self.vacancy_tests is not None and response_url:
            try:
                tests_data_dict = self.vacancy_tests.fetch_tests(response_url)
                test_data = tests_data_dict.get(str(vacancy_id))
                if test_data is None:
                    test_status = "manual_required"
                else:
                    generated_answers = self.vacancy_tests.prepare_answers(
                        test_data
                    )
                    test_status = "generated"
            except Exception as ex:  # noqa: BLE001
                logger.warning(
                    "Не удалось загрузить тесты для вакансии %s: %s",
                    vacancy_id,
                    ex,
                )
                test_status = "manual_required"
        elif has_test:
            test_status = "manual_required"

        # 4. Save draft
        draft = ApplicationDraftModel(
            search_profile_id=(search_profile.id if search_profile else None),
            resume_id=str(resume_id) if resume_id else "",
            vacancy_id=int(vacancy_id) if vacancy_id else 0,
            employer_id=int(employer_id) if employer_id else None,
            status=status,
            relevance_score=relevance_score,
            relevance_reason=relevance_reason,
            analysis_json=analysis_json,
            full_vacancy_json=vacancy,
            cover_letter=cover_letter,
            cover_letter_status=cover_letter_status,
            has_test=has_test,
            test_status=test_status,
        )
        self.storage.application_drafts.save(draft)

        # 5. Save generated test answers (issue #5).
        if generated_answers:
            try:
                saved_draft = (
                    self.storage.application_drafts.get_by_resume_vacancy(
                        str(resume_id or ""), int(vacancy_id or 0)
                    )
                )
            except RepositoryError as ex:
                logger.warning(
                    "Не удалось перечитать черновик для привязки "
                    "тест-ответов: %s",
                    ex,
                )
                saved_draft = None
            if saved_draft is not None and saved_draft.id is not None:
                for answer in generated_answers:
                    answer.draft_id = saved_draft.id
                    try:
                        self.storage.application_test_answers.save(answer)
                    except RepositoryError as ex:
                        logger.warning(
                            "Не удалось сохранить ответ на тест %s: %s",
                            getattr(answer, "task_id", "?"),
                            ex,
                        )
        return draft


def _analysis_to_dict(result: Any) -> dict:
    """Convert ``RelevanceResult`` to dict for ``analysis_json``.

    .. deprecated::
        Use :func:`job_bot.application_prep.utils.analysis_to_dict` instead.
        Kept as a thin wrapper for backward compatibility (issue #54).
    """
    return analysis_to_dict(result)
