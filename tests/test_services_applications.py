"""Тесты оркестратора черновиков откликов (issue #3)."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

from hh_applicant_tool.services.applications import ApplicationsService
from hh_applicant_tool.services.relevance import RelevanceResult
from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.search_profile import SearchProfileModel

# ─── Базовые сценарии (без AI, без писем) ───────────────────────────


def _make_facade(conn: sqlite3.Connection) -> StorageFacade:
    return StorageFacade(conn)


def test_prepare_one_no_ai_no_letter_saves_prepared(
    storage: sqlite3.Connection,
):
    """Без AI и без cover_letter-сервиса — статус prepared, draft сохранён."""
    facade = _make_facade(storage)
    svc = ApplicationsService(facade)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "employer": {"id": 42}},
        placeholders={
            "first_name": "И",
            "vacancy_name": "V",
            "resume_title": "T",
        },
        force_message=True,
    )

    assert draft is not None
    assert draft.status == "prepared"
    # cover_letter-сервис не подключён, значит письма нет
    assert draft.cover_letter is None
    assert draft.cover_letter_status is None
    storage.commit()

    saved = facade.application_drafts.get_by_resume_vacancy("r1", 1)
    assert saved is not None
    assert saved.status == "prepared"


def test_prepare_one_without_cover_letter_service(storage: sqlite3.Connection):
    """Без cover_letter-сервиса cover_letter=None."""
    facade = _make_facade(storage)
    svc = ApplicationsService(facade)
    svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        placeholders={"first_name": "И", "vacancy_name": "V"},
    )
    storage.commit()

    saved = facade.application_drafts.get_by_resume_vacancy("r1", 1)
    assert saved.cover_letter is None
    assert saved.cover_letter_status is None


# ─── AI-сценарии ────────────────────────────────────────────────────


def _make_relevance_svc(suitable: bool, **kwargs) -> MagicMock:
    """Возвращает MagicMock, у которого is_suitable_heavy/light = заданный результат."""
    result = RelevanceResult(
        suitable=suitable,
        score=kwargs.get("score", 80 if suitable else 20),
        reason=kwargs.get("reason", "ok" if suitable else "wrong stack"),
        raw_response=kwargs.get("raw_response", "raw"),
    )
    svc = MagicMock()
    svc.is_suitable_heavy.return_value = result
    svc.is_suitable_light.return_value = result
    return svc


def test_prepare_one_ai_heavy_rejected(storage: sqlite3.Connection):
    facade = _make_facade(storage)
    relevance = _make_relevance_svc(
        suitable=False, score=20, reason="wrong stack"
    )
    svc = ApplicationsService(facade, relevance=relevance)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "employer": {"id": 42}},
        ai_filter_mode="heavy",
    )
    storage.commit()

    assert draft is not None
    assert draft.status == "rejected"
    assert draft.relevance_score == 20
    assert draft.relevance_reason == "wrong stack"
    assert draft.cover_letter is None
    assert draft.cover_letter_status is None
    assert draft.analysis_json is not None
    assert draft.analysis_json["suitable"] is False


def test_prepare_one_ai_light_accepted(storage: sqlite3.Connection):
    facade = _make_facade(storage)
    relevance = _make_relevance_svc(
        suitable=True, score=85, reason="matches stack"
    )
    svc = ApplicationsService(facade, relevance=relevance)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "employer": {"id": 42}},
        ai_filter_mode="light",
    )
    storage.commit()

    assert draft is not None
    assert draft.status == "prepared"
    assert draft.relevance_score == 85
    assert draft.relevance_reason == "matches stack"
    assert draft.analysis_json is not None
    assert draft.analysis_json["suitable"] is True


def test_prepare_one_ai_filter_heavy_uses_heavy(storage: sqlite3.Connection):
    """При mode=heavy вызывается is_suitable_heavy, не light."""
    facade = _make_facade(storage)
    relevance = MagicMock()
    relevance.is_suitable_heavy.return_value = RelevanceResult(
        suitable=True, score=90, reason="ok"
    )
    relevance.is_suitable_light.return_value = RelevanceResult(
        suitable=False, score=10, reason="no"
    )
    svc = ApplicationsService(facade, relevance=relevance)

    svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        ai_filter_mode="heavy",
    )

    relevance.is_suitable_heavy.assert_called_once()
    relevance.is_suitable_light.assert_not_called()


def test_prepare_one_ai_filter_light_uses_light(storage: sqlite3.Connection):
    facade = _make_facade(storage)
    relevance = MagicMock()
    relevance.is_suitable_heavy.return_value = RelevanceResult(
        suitable=False, score=10, reason="no"
    )
    relevance.is_suitable_light.return_value = RelevanceResult(
        suitable=True, score=80, reason="ok"
    )
    svc = ApplicationsService(facade, relevance=relevance)

    svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        ai_filter_mode="light",
    )

    relevance.is_suitable_light.assert_called_once()
    relevance.is_suitable_heavy.assert_not_called()


def test_prepare_one_no_ai_filter_skips_relevance(
    storage: sqlite3.Connection,
):
    """ai_filter_mode=None — relevance.is_suitable_* не зовётся."""
    facade = _make_facade(storage)
    relevance = MagicMock()
    svc = ApplicationsService(facade, relevance=relevance)

    svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        ai_filter_mode=None,
    )
    storage.commit()

    relevance.is_suitable_heavy.assert_not_called()
    relevance.is_suitable_light.assert_not_called()


# ─── Cover letter ──────────────────────────────────────────────────


def test_prepare_one_with_cover_letter_service_ai(
    storage: sqlite3.Connection,
):
    facade = _make_facade(storage)
    cover = MagicMock()
    cover.generate.return_value = "AI LETTER"
    svc = ApplicationsService(facade, cover_letter=cover)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        placeholders={"first_name": "И", "vacancy_name": "V"},
        force_message=True,
    )
    storage.commit()

    assert draft.cover_letter == "AI LETTER"
    assert draft.cover_letter_status == "generated"
    cover.generate.assert_called_once()
    # Аргументы generate
    call = cover.generate.call_args
    assert call.kwargs.get("force") is True
    # required_by_vacancy берётся из vacancy.response_letter_required
    assert call.kwargs.get("required_by_vacancy") is False


def test_prepare_one_cover_letter_required_by_vacancy(
    storage: sqlite3.Connection,
):
    """vacancy.response_letter_required=True пробрасывается в cover.generate."""
    facade = _make_facade(storage)
    cover = MagicMock()
    cover.generate.return_value = "Required letter"
    svc = ApplicationsService(facade, cover_letter=cover)

    svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "response_letter_required": True},
        placeholders={"first_name": "И"},
    )

    call = cover.generate.call_args
    assert call.kwargs.get("required_by_vacancy") is True


def test_prepare_one_cover_letter_failure_marks_failed(
    storage: sqlite3.Connection,
):
    facade = _make_facade(storage)
    cover = MagicMock()
    cover.generate.side_effect = RuntimeError("AI down")
    svc = ApplicationsService(facade, cover_letter=cover)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        placeholders={"first_name": "И"},
        force_message=True,
    )
    storage.commit()

    # Вакансия не отклонена, статус prepared
    assert draft.status == "prepared"
    # cover_letter is None, но статус 'failed'
    assert draft.cover_letter is None
    assert draft.cover_letter_status == "failed"


# ─── Tests вакансий ────────────────────────────────────────────────


def test_prepare_one_has_test_no_service_marks_manual_required(
    storage: sqlite3.Connection,
):
    facade = _make_facade(storage)
    svc = ApplicationsService(facade)  # vacancy_tests=None

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "has_test": True},
        placeholders={"first_name": "И"},
    )
    storage.commit()

    assert draft.has_test is True
    assert draft.test_status == "manual_required"


def test_prepare_one_has_test_with_service_generated(
    storage: sqlite3.Connection,
):
    facade = _make_facade(storage)
    vacancy_tests = MagicMock()
    vacancy_tests.fetch_tests.return_value = {
        "1": {
            "uidPk": "u",
            "guid": "g",
            "startTime": "t",
            "required": "true",
            "tasks": [],
        }
    }
    svc = ApplicationsService(facade, vacancy_tests=vacancy_tests)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "has_test": True},
        placeholders={"first_name": "И"},
        response_url="https://hh.ru/applicant/vacancy_response?vacancyId=1",
    )
    storage.commit()

    assert draft.has_test is True
    assert draft.test_status == "generated"
    vacancy_tests.fetch_tests.assert_called_once()


def test_prepare_one_has_test_with_service_test_data_missing(
    storage: sqlite3.Connection,
):
    """Если в fetch_tests нет данных для нужной вакансии — manual_required."""
    facade = _make_facade(storage)
    vacancy_tests = MagicMock()
    vacancy_tests.fetch_tests.return_value = {}  # пусто
    svc = ApplicationsService(facade, vacancy_tests=vacancy_tests)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "has_test": True},
        placeholders={"first_name": "И"},
        response_url="https://hh.ru/applicant/vacancy_response?vacancyId=1",
    )

    assert draft.test_status == "manual_required"


def test_prepare_one_has_test_with_service_failure(
    storage: sqlite3.Connection,
):
    """Ошибка при fetch_tests → manual_required."""
    facade = _make_facade(storage)
    vacancy_tests = MagicMock()
    vacancy_tests.fetch_tests.side_effect = RuntimeError("boom")
    svc = ApplicationsService(facade, vacancy_tests=vacancy_tests)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "has_test": True},
        placeholders={"first_name": "И"},
        response_url="https://hh.ru/applicant/vacancy_response?vacancyId=1",
    )

    assert draft.test_status == "manual_required"


def test_prepare_one_has_test_no_response_url(
    storage: sqlite3.Connection,
):
    """Без response_url — manual_required, даже при наличии сервиса."""
    facade = _make_facade(storage)
    vacancy_tests = MagicMock()
    svc = ApplicationsService(facade, vacancy_tests=vacancy_tests)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "has_test": True},
        placeholders={"first_name": "И"},
        # response_url=None
    )

    assert draft.test_status == "manual_required"
    vacancy_tests.fetch_tests.assert_not_called()


def test_prepare_one_no_test_no_test_status(storage: sqlite3.Connection):
    """Без has_test — test_status не выставляется."""
    facade = _make_facade(storage)
    svc = ApplicationsService(facade)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        placeholders={"first_name": "И"},
    )
    storage.commit()

    assert draft.has_test is False
    assert draft.test_status is None


# ─── Search profile ────────────────────────────────────────────────


def test_prepare_one_search_profile_id(storage: sqlite3.Connection):
    facade = _make_facade(storage)
    svc = ApplicationsService(facade)

    profile = SearchProfileModel(
        id="django-senior", name="Django Senior", resume_id="r1"
    )
    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        search_profile=profile,
        placeholders={"first_name": "И"},
    )
    storage.commit()

    assert draft.search_profile_id == "django-senior"


def test_prepare_one_no_search_profile(storage: sqlite3.Connection):
    facade = _make_facade(storage)
    svc = ApplicationsService(facade)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        placeholders={"first_name": "И"},
    )
    storage.commit()

    assert draft.search_profile_id is None


# ─── Upsert ─────────────────────────────────────────────────────────


def test_prepare_one_upserts_existing_draft(storage: sqlite3.Connection):
    """Повторный вызов для той же пары (resume, vacancy) обновляет запись."""
    facade = _make_facade(storage)
    svc = ApplicationsService(facade)

    svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        placeholders={"first_name": "И"},
    )
    svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        placeholders={"first_name": "И"},
    )
    storage.commit()

    assert facade.application_drafts.count_total() == 1


# ─── Доп. поля черновика ────────────────────────────────────────────


def test_prepare_one_stores_employer_id(storage: sqlite3.Connection):
    facade = _make_facade(storage)
    svc = ApplicationsService(facade)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V", "employer": {"id": 9999}},
        placeholders={"first_name": "И"},
    )
    storage.commit()

    assert draft.employer_id == 9999


def test_prepare_one_stores_full_vacancy(storage: sqlite3.Connection):
    """vacancy целиком сохраняется в full_vacancy_json."""
    facade = _make_facade(storage)
    svc = ApplicationsService(facade)

    vacancy = {
        "id": 1,
        "name": "V",
        "employer": {"id": 42, "name": "Acme"},
        "snippet": {"requirement": "Python"},
    }
    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy=vacancy,
        placeholders={"first_name": "И"},
    )
    storage.commit()

    assert draft.full_vacancy_json is not None
    assert draft.full_vacancy_json["id"] == 1
    assert draft.full_vacancy_json["employer"]["name"] == "Acme"


def test_prepare_one_with_relevance_service_stores_analysis(
    storage: sqlite3.Connection,
):
    """analysis_json содержит suitable/score/reason/raw_response."""
    facade = _make_facade(storage)
    relevance = _make_relevance_svc(
        suitable=True, score=88, reason="matches", raw_response="RAW"
    )
    svc = ApplicationsService(facade, relevance=relevance)

    draft = svc.prepare_one(
        resume={"id": "r1", "title": "T"},
        vacancy={"id": 1, "name": "V"},
        ai_filter_mode="heavy",
    )
    storage.commit()

    assert draft.analysis_json is not None
    assert draft.analysis_json["suitable"] is True
    assert draft.analysis_json["score"] == 88
    assert draft.analysis_json["reason"] == "matches"
    assert draft.analysis_json["raw_response"] == "RAW"
