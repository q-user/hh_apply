"""Тесты репозитория application_test_answers (issue #1)."""

from __future__ import annotations

import sqlite3

from job_bot._legacy_compat.storage.facade import StorageFacade
from job_bot._legacy_compat.storage.models.application_test_answer import (
    ApplicationTestAnswerModel,
)


def _make_draft(facade: StorageFacade, vacancy_id: int = 100) -> int:
    facade.application_drafts.save(
        facade.application_drafts.model(
            resume_id="r1", vacancy_id=vacancy_id, status="prepared"
        )
    )
    storage = facade.application_drafts.conn
    storage.commit()
    row = storage.execute(
        "SELECT id FROM application_drafts WHERE vacancy_id=?", (vacancy_id,)
    ).fetchone()
    return row["id"]


def test_insert_and_find_by_draft(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade)

    facade.application_test_answers.save(
        ApplicationTestAnswerModel(
            draft_id=draft_id,
            task_id="task-1",
            question="Какой HTTP-код?",
            answer_type="choice",
            options_json=["200", "201", "404"],
            generated_answer="201",
            selected_solution_id="201",
            review_status="approved",
        )
    )
    storage.commit()

    answers = facade.application_test_answers.find_by_draft(draft_id)
    assert len(answers) == 1
    a = answers[0]
    assert a.task_id == "task-1"
    assert a.options_json == ["200", "201", "404"]
    assert a.review_status == "approved"


def test_upsert_on_draft_task_conflict(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade)

    facade.application_test_answers.save(
        ApplicationTestAnswerModel(
            draft_id=draft_id,
            task_id="t1",
            generated_answer="v1",
            review_status="generated",
        )
    )
    facade.application_test_answers.save(
        ApplicationTestAnswerModel(
            draft_id=draft_id,
            task_id="t1",
            generated_answer="v2 — улучшено",
            review_status="regenerated",
        )
    )
    storage.commit()

    assert facade.application_test_answers.count_total() == 1
    answers = facade.application_test_answers.find_by_draft(draft_id)
    assert answers[0].generated_answer == "v2 — улучшено"
    assert answers[0].review_status == "regenerated"


def test_multiple_tasks_per_draft(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    draft_id = _make_draft(facade)

    for i in range(3):
        facade.application_test_answers.save(
            ApplicationTestAnswerModel(
                draft_id=draft_id,
                task_id=f"t{i}",
                generated_answer=f"answer {i}",
            )
        )
    storage.commit()

    assert facade.application_test_answers.count_total() == 3
    answers = facade.application_test_answers.find_by_draft(draft_id)
    task_ids = {a.task_id for a in answers}
    assert task_ids == {"t0", "t1", "t2"}


def test_delete_by_draft(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    d1 = _make_draft(facade, vacancy_id=1)
    d2 = _make_draft(facade, vacancy_id=2)

    facade.application_test_answers.save(
        ApplicationTestAnswerModel(draft_id=d1, task_id="t")
    )
    facade.application_test_answers.save(
        ApplicationTestAnswerModel(draft_id=d2, task_id="t")
    )
    storage.commit()
    assert facade.application_test_answers.count_total() == 2

    facade.application_test_answers.delete_by_draft(d1)
    storage.commit()
    assert facade.application_test_answers.count_total() == 1
    remaining = facade.application_test_answers.find_by_draft(d2)
    assert len(remaining) == 1
