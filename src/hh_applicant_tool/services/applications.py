"""Общий pipeline подготовки черновика отклика.

Извлечено из ``operations/apply_vacancies.py`` (issue #3). Сервис
оркестрирует:

1. AI-фильтрация вакансии (:class:`RelevanceService`).
2. Генерация сопроводительного письма (:class:`CoverLetterService`).
3. (Опционально) ответы на тесты (:class:`VacancyTestsService`).
4. Сохранение :class:`ApplicationDraftModel` со статусом ``"prepared"``
   (или ``"rejected"`` если AI-фильтр отклонил).

Используется из будущего ``prepare-vacancies`` (issue #5) и опционально
из ``apply-vacancies`` (после рефакторинга).
"""

from __future__ import annotations

import logging
from typing import Any


from ..storage.facade import StorageFacade
from ..storage.models.application_draft import ApplicationDraftModel
from ..storage.models.search_profile import SearchProfileModel
from ..storage.repositories.errors import RepositoryError
from .relevance import RelevanceService

logger = logging.getLogger(__package__)


class ApplicationsService:
    """Подготовка одного черновика отклика ``(resume, vacancy) -> draft``.

    Attributes:
        storage: фасад хранилища (для upsert в ``application_drafts``).
        relevance: сервис AI-фильтрации (или ``None`` — пропускаем фильтр).
        cover_letter: сервис генерации письма (или ``None``).
        vacancy_tests: сервис тестов вакансии (или ``None`` — пропускаем
            генерацию ответов).
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
        """Подготавливает (или обновляет) черновик отклика.

        Возвращает:
        - ``ApplicationDraftModel`` со статусом ``"prepared"`` — если вакансия
          прошла AI-фильтр;
        - ``ApplicationDraftModel`` со статусом ``"rejected"`` — если
          AI-фильтр отклонил (только score/reason/relevance_reason
          заполнены, ``cover_letter`` пустой);
        - ``None`` — если вакансия вообще не заинтересовала (например,
          ``response_url`` отсутствует и пришёл сигнал пропустить).

        Аргументы:
        - ``resume``: dict (datatypes.Resume);
        - ``vacancy``: dict (datatypes.SearchVacancy);
        - ``search_profile``: опционально — для ``search_profile_id``;
        - ``resume_analysis``: текст анализа резюме (используется в письме);
        - ``ai_filter_mode``: ``"heavy"`` / ``"light"`` / ``None``;
        - ``placeholders``: ``first_name``/``last_name``/``resume_title``
          и т.п. для шаблона письма;
        - ``force_message``: всегда генерировать письмо;
        - ``response_url``: URL страницы тестов (если у вакансии есть
          ``has_test``). Если не передан и ``has_test=True`` — черновик
          помечается ``test_status='manual_required'`` без генерации.
        """
        resume_id = resume.get("id")
        vacancy_id = vacancy.get("id")
        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")

        # 1. AI-фильтрация (если включена)
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

        # Если вакансия отклонена AI — сохраняем rejected-draft и выходим
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

        # 2. Генерация письма
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
            except Exception as ex:
                logger.warning(
                    "Не удалось сгенерировать сопроводительное письмо: %s",
                    ex,
                )
                cover_letter_status = "failed"

        # 3. Тесты вакансии (без HTTP-отправки)
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
                    # Готовим ответы AI/rule-based. Их нужно сохранить
                    # после ``storage.application_drafts.save(draft)``,
                    # потому что ``draft.id`` появляется только после UPSERT.
                    generated_answers = self.vacancy_tests.prepare_answers(
                        test_data
                    )
                    test_status = "generated"
            except Exception as ex:
                logger.warning(
                    "Не удалось загрузить тесты для вакансии %s: %s",
                    vacancy_id,
                    ex,
                )
                test_status = "manual_required"
        elif has_test:
            test_status = "manual_required"

        # 4. Сохранение draft
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

        # 5. Сохранение сгенерированных ответов на тесты (issue #5).
        # ``draft.id`` известен только после UPSERT — перечитываем запись.
        # Если сохранение не удалось — черновик остаётся (статус
        # ``generated``), а тест-ответы можно перегенерировать отдельно.
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
    """Превращает ``RelevanceResult`` в dict для ``analysis_json``.

    Не импортируем :class:`RelevanceResult` напрямую, чтобы не зацикливать
    зависимости и принимать любой duck-typed объект (dataclass / NamedTuple).
    """
    out: dict = {"suitable": bool(getattr(result, "suitable", False))}
    score = getattr(result, "score", None)
    if score is not None:
        out["score"] = score
    reason = getattr(result, "reason", None)
    if reason is not None:
        out["reason"] = reason
    raw = getattr(result, "raw_response", None)
    if raw is not None:
        out["raw_response"] = raw
    return out
