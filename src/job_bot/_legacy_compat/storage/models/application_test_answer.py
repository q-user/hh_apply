from __future__ import annotations

from typing import Any

from .base import BaseModel, mapped


class ApplicationTestAnswerModel(BaseModel):
    """Ответ на тест HH, привязанный к черновику.

    `options_json` хранит массив доступных вариантов (для choice/multi_choice).
    `generated_answer` — то, что сгенерировал AI; `review_status` отражает
    ручное одобрение/правки. `selected_solution_id` — индекс/идентификатор
    выбранного варианта для choice-ответов.
    """

    id: int | None = None
    draft_id: int
    task_id: str
    question: str | None = None
    answer_type: str | None = None
    options_json: Any = mapped(store_json=True, default=None)
    generated_answer: str | None = None
    selected_solution_id: str | None = None
    review_status: str = "generated"
    reviewer_comment: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
