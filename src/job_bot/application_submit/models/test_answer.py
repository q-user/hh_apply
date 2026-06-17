"""TestAnswer DTO for vacancy tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TestAnswerType:
    """Test answer types -- mirrors the storage model values."""

    CHOICE = "choice"
    TEXT = "text"


@dataclass
class TestAnswer:
    """A single test-answer DTO used by the slice.

    Mirrors :class:`ApplicationTestAnswerModel` but lives in the slice
    so we don't depend on the storage model from the public API.
    """

    task_id: str
    question: str | None = None
    answer_type: str | None = None
    options_json: list[dict[str, Any]] | None = None
    generated_answer: str | None = None
    selected_solution_id: str | None = None
    review_status: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dict (used for logging / persistence)."""
        return {
            "task_id": self.task_id,
            "question": self.question,
            "answer_type": self.answer_type,
            "options_json": self.options_json,
            "generated_answer": self.generated_answer,
            "selected_solution_id": self.selected_solution_id,
            "review_status": self.review_status,
        }


__all__ = ["TestAnswer", "TestAnswerType"]
