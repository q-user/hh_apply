"""Тесты репозитория search_profiles (issue #3)."""

from __future__ import annotations

import sqlite3

from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.search_profile import SearchProfileModel


def test_insert_and_get(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    profile = SearchProfileModel(
        id="django-senior",
        name="Django Senior",
        resume_id="r1",
        enabled=True,
        ai_filter_mode="heavy",
    )
    facade.search_profiles.save(profile)
    storage.commit()

    fetched = facade.search_profiles.get("django-senior")
    assert fetched is not None
    assert fetched.id == "django-senior"
    assert fetched.name == "Django Senior"
    assert fetched.resume_id == "r1"
    assert fetched.enabled is True
    assert fetched.ai_filter_mode == "heavy"


def test_default_enabled_is_true(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.search_profiles.save(
        SearchProfileModel(id="p1", name="P1", resume_id="r1")
    )
    storage.commit()

    fetched = facade.search_profiles.get("p1")
    assert fetched.enabled is True
    assert fetched.search_params is None
    assert fetched.relevance_rules is None
    assert fetched.ai_filter_mode is None


def test_upsert_on_id_conflict(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.search_profiles.save(
        SearchProfileModel(
            id="p1",
            name="Initial",
            resume_id="r1",
            enabled=True,
            ai_filter_mode="heavy",
        )
    )
    facade.search_profiles.save(
        SearchProfileModel(
            id="p1",
            name="Updated",
            resume_id="r2",
            enabled=False,
            ai_filter_mode="light",
        )
    )
    storage.commit()

    # Одна запись, поля обновлены
    assert facade.search_profiles.count_total() == 1
    fetched = facade.search_profiles.get("p1")
    assert fetched.name == "Updated"
    assert fetched.resume_id == "r2"
    assert fetched.enabled is False
    assert fetched.ai_filter_mode == "light"


def test_json_fields_roundtrip(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    search_params = {
        "text": "Python Developer",
        "area": ["1", "2"],
        "salary": 250000,
        "experience": "between3And6",
        "employment": ["full"],
    }
    relevance_rules = {
        "min_score": 70,
        "exclude_keywords": ["1С", "PHP"],
        "require_keywords": ["Python", "Django"],
    }
    facade.search_profiles.save(
        SearchProfileModel(
            id="p1",
            name="P1",
            resume_id="r1",
            search_params=search_params,
            relevance_rules=relevance_rules,
        )
    )
    storage.commit()

    fetched = facade.search_profiles.get("p1")
    assert fetched.search_params == search_params
    assert fetched.relevance_rules == relevance_rules


def test_find_enabled(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.search_profiles.save(
        SearchProfileModel(id="p1", name="P1", resume_id="r1", enabled=True)
    )
    facade.search_profiles.save(
        SearchProfileModel(id="p2", name="P2", resume_id="r1", enabled=False)
    )
    facade.search_profiles.save(
        SearchProfileModel(id="p3", name="P3", resume_id="r1", enabled=True)
    )
    storage.commit()

    enabled = sorted(p.id for p in facade.search_profiles.find_enabled())
    assert enabled == ["p1", "p3"]


def test_find_by_enabled_and_resume(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.search_profiles.save(
        SearchProfileModel(id="p1", name="P1", resume_id="r1", enabled=True)
    )
    facade.search_profiles.save(
        SearchProfileModel(id="p2", name="P2", resume_id="r2", enabled=True)
    )
    facade.search_profiles.save(
        SearchProfileModel(id="p3", name="P3", resume_id="r1", enabled=False)
    )
    storage.commit()

    r1_profiles = list(
        facade.search_profiles.find(resume_id="r1", enabled=True)
    )
    assert {p.id for p in r1_profiles} == {"p1"}


def test_delete(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.search_profiles.save(
        SearchProfileModel(id="p1", name="P1", resume_id="r1")
    )
    storage.commit()
    assert facade.search_profiles.count_total() == 1

    facade.search_profiles.delete(facade.search_profiles.get("p1"))
    storage.commit()
    assert facade.search_profiles.count_total() == 0
    assert facade.search_profiles.get("p1") is None


def test_trigger_updates_updated_at(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    facade.search_profiles.save(
        SearchProfileModel(id="p1", name="P1", resume_id="r1", enabled=True)
    )
    storage.commit()

    row = storage.execute(
        "SELECT updated_at FROM search_profiles WHERE id='p1'"
    ).fetchone()
    initial = row["updated_at"]
    assert initial is not None

    facade.search_profiles.save(
        SearchProfileModel(id="p1", name="P1", resume_id="r1", enabled=False)
    )
    storage.commit()

    row = storage.execute(
        "SELECT updated_at FROM search_profiles WHERE id='p1'"
    ).fetchone()
    assert row["updated_at"] >= initial


def test_get_returns_none_for_missing(storage: sqlite3.Connection):
    facade = StorageFacade(storage)
    assert facade.search_profiles.get("nope") is None
