from __future__ import annotations

from ..models.application_draft import ApplicationDraftModel
from .base import BaseRepository


class ApplicationDraftsRepository(BaseRepository):
    __table__ = "application_drafts"
    model = ApplicationDraftModel
    # Один черновик на пару (resume_id, vacancy_id) — UPSERT при перезапуске
    # prepare-vacancies обновляет существующую запись вместо ошибки.
    conflict_columns = ("resume_id", "vacancy_id")

    def get_by_resume_vacancy(
        self, resume_id: str, vacancy_id: int
    ) -> ApplicationDraftModel | None:
        """Возвращает черновик для конкретной пары (resume, vacancy) или None."""
        return next(
            self.find(resume_id=resume_id, vacancy_id=vacancy_id),
            None,
        )

    def delete_by_resume_vacancy(
        self, resume_id: str, vacancy_id: int, /
    ) -> bool:
        """Удаляет черновик для пары (resume, vacancy). Возвращает True, если
        запись существовала."""
        draft = self.get_by_resume_vacancy(resume_id, vacancy_id)
        if draft is None:
            return False
        self.delete(draft)
        return True
