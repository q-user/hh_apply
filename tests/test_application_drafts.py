"""Тесты репозитория application_drafts (issue #1)."""

from __future__ import annotations

import sqlite3

from job_bot._legacy_compat.storage.facade import StorageFacade
from job_bot._legacy_compat.storage.models.application_draft import (
    ApplicationDraftModel,
)


def test_insert_and_get(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    draft = ApplicationDraftModel(
        search_profile_id="django-senior",
        resume_id="r1",
        vacancy_id=100,
        employer_id=42,
        status="prepared",
        relevance_score=88,
        success_probability=72,
        relevance_reason="Primary stack совпадает",
        cover_letter="Здравствуйте!",
        cover_letter_status="generated",
        has_test=True,
        test_status="generated",
    )
    facade.application_drafts.save(draft)
    storage.commit()

    fetched = facade.application_drafts.get_by_resume_vacancy("r1", 100)
    assert fetched is not None
    assert fetched.resume_id == "r1"
    assert fetched.vacancy_id == 100
    assert fetched.status == "prepared"
    assert fetched.relevance_score == 88
    assert fetched.has_test is True


def test_upsert_on_resume_vacancy_conflict(
    storage: sqlite3.Connection,
):
    facade = StorageFacade(storage)
    facade.application_drafts.save(
        ApplicationDraftModel(
            resume_id="r1",
            vacancy_id=100,
            status="prepared",
            relevance_score=80,
        )
    )
    facade.application_drafts.save(
        ApplicationDraftModel(
            resume_id="r1",
            vacancy_id=100,
            status="approved",
            relevance_score=95,
        )
    )
    storage.commit()

    # Должна остаться ровно одна запись, статус и score обновлены
    assert facade.application_drafts.count_total() == 1
    fetched = facade.application_drafts.get_by_resume_vacancy("r1", 100)
    assert fetched.status == "approved"
    assert fetched.relevance_score == 95


def test_json_fields_roundtrip(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    analysis = {
        "suitable": True,
        "relevance_score": 92,
        "primary_stack": ["Python", "Django"],
        "risks": ["FastAPI mentioned"],
    }
    full = {"id": 100, "name": "Senior", "salary": {"from": 250000}}
    facade.application_drafts.save(
        ApplicationDraftModel(
            resume_id="r1",
            vacancy_id=100,
            status="prepared",
            analysis_json=analysis,
            full_vacancy_json=full,
        )
    )
    storage.commit()

    fetched = facade.application_drafts.get_by_resume_vacancy("r1", 100)
    assert fetched.analysis_json == analysis
    assert fetched.full_vacancy_json == full


def test_find_by_status(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.application_drafts.save(
        ApplicationDraftModel(resume_id="r1", vacancy_id=1, status="prepared")
    )
    facade.application_drafts.save(
        ApplicationDraftModel(resume_id="r1", vacancy_id=2, status="approved")
    )
    facade.application_drafts.save(
        ApplicationDraftModel(resume_id="r1", vacancy_id=3, status="skipped")
    )
    storage.commit()

    prepared = list(facade.application_drafts.find(status="prepared"))
    assert len(prepared) == 1
    assert prepared[0].vacancy_id == 1

    approved = list(facade.application_drafts.find(status="approved"))
    assert len(approved) == 1
    assert approved[0].vacancy_id == 2


def test_find_by_profile(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.application_drafts.save(
        ApplicationDraftModel(
            resume_id="r1", vacancy_id=1, search_profile_id="django"
        )
    )
    facade.application_drafts.save(
        ApplicationDraftModel(
            resume_id="r1", vacancy_id=2, search_profile_id="fastapi"
        )
    )
    storage.commit()

    django_drafts = list(
        facade.application_drafts.find(search_profile_id="django")
    )
    assert len(django_drafts) == 1
    assert django_drafts[0].vacancy_id == 1


def test_delete(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.application_drafts.save(
        ApplicationDraftModel(resume_id="r1", vacancy_id=1)
    )
    storage.commit()
    assert facade.application_drafts.count_total() == 1

    facade.application_drafts.delete_by_resume_vacancy("r1", 1)
    storage.commit()
    assert facade.application_drafts.count_total() == 0


def test_trigger_updates_updated_at(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.application_drafts.save(
        ApplicationDraftModel(resume_id="r1", vacancy_id=1, status="new")
    )
    storage.commit()

    row = storage.execute(
        "SELECT updated_at FROM application_drafts WHERE resume_id='r1'"
    ).fetchone()
    initial = row["updated_at"]
    assert initial is not None

    facade.application_drafts.save(
        ApplicationDraftModel(resume_id="r1", vacancy_id=1, status="prepared")
    )
    storage.commit()

    row = storage.execute(
        "SELECT updated_at FROM application_drafts WHERE resume_id='r1'"
    ).fetchone()
    assert row["updated_at"] >= initial
