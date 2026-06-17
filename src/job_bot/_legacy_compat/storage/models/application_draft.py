from __future__ import annotations

from typing import Any

from .base import BaseModel, mapped


class ApplicationDraftModel(BaseModel):
    """Подготовленный черновик отклика: один на пару (resume_id, vacancy_id).

    Хранит и релевантность (relevance_score, success_probability), и артефакты
    подготовки (cover_letter, has_test, test_status), и состояние конвейера
    (status). Отправкой занимается apply-worker через отдельную таблицу
    apply_jobs.
    """

    id: int | None = None
    search_profile_id: str | None = None
    resume_id: str
    vacancy_id: int
    employer_id: int | None = None
    status: str = "new"
    relevance_score: int | None = None
    success_probability: int | None = None
    relevance_reason: str | None = None
    analysis_json: Any = mapped(store_json=True, default=None)
    full_vacancy_json: Any = mapped(store_json=True, default=None)
    cover_letter: str | None = None
    cover_letter_status: str | None = None
    has_test: bool = False
    test_status: str | None = None
    hh_response_url: str | None = None
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
