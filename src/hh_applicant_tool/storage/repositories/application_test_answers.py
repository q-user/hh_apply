from __future__ import annotations

from ..models.application_test_answer import ApplicationTestAnswerModel
from .base import BaseRepository


class ApplicationTestAnswersRepository(BaseRepository):
    __table__ = "application_test_answers"
    model = ApplicationTestAnswerModel
    # Один ответ на (черновик, задача) — повторная генерация обновляет запись.
    conflict_columns = ("draft_id", "task_id")

    def find_by_draft(self, draft_id: int) -> list[ApplicationTestAnswerModel]:
        """Возвращает все ответы тестов для черновика в порядке создания."""
        return list(self.find(draft_id=draft_id))

    def delete_by_draft(self, draft_id: int, /) -> int:
        """Удаляет все ответы тестов черновика. Возвращает кол-во удалённых."""
        cur = self.conn.execute(
            "DELETE FROM application_test_answers WHERE draft_id = ?",
            (draft_id,),
        )
        self.maybe_commit()
        return cur.rowcount
