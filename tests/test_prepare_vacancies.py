"""Тесты use case ``PrepareVacanciesUseCase`` (issue #5).

Сценарии:
- 1. Вакансия без теста → черновик ``prepared`` с cover_letter,
     relevance_score/reason, search_profile_id, без ``api_client.post``.
- 2. Вакансия с тестом → ``application_test_answers`` заполнены,
     ``test_status='generated'``.
- 3. AI отклоняет → ``skipped_vacancies.reason == 'ai_rejected'``.
- 4. Повторный запуск → один черновик (UPSERT), ответы тоже UPSERT.
- 5. ``--dry-run`` → ни одной записи в БД.
- 6. ``--search-profile p1`` → обрабатывается только p1.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import MagicMock

from hh_applicant_tool.application.dto import (
    PrepareVacanciesCommand,
    PrepareVacanciesResult,
)
from hh_applicant_tool.application.use_cases.prepare_vacancies import (
    PrepareVacanciesUseCase,
)
from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.search_profile import SearchProfileModel
from job_bot.application_prep.models.relevance import RelevanceResult

# ─── Helpers ───────────────────────────────────────────────────────


def _make_facade(conn: sqlite3.Connection) -> StorageFacade:
    return StorageFacade(conn)


def _profile(
    id_: str = "django-senior",
    *,
    resume_id: str = "r1",
    enabled: bool = True,
    ai_filter_mode: str | None = "heavy",
    search_params: dict[str, Any] | None = None,
) -> SearchProfileModel:
    # Default: пустые search_params → use case идёт через
    # ``/resumes/{id}/similar_vacancies`` (если text не задан).
    return SearchProfileModel(
        id=id_,
        name=id_,
        resume_id=resume_id,
        enabled=enabled,
        ai_filter_mode=ai_filter_mode,
        search_params=search_params or {},
    )


def _resume(id_: str = "r1", *, status: str = "published") -> dict[str, Any]:
    return {
        "id": id_,
        "title": "Backend",
        "status": {"id": status},
        "alternate_url": f"https://hh.ru/resume/{id_}",
    }


def _vacancy(
    vid: int = 1,
    *,
    name: str = "Senior Python",
    employer_id: int | None = 42,
    archived: bool = False,
    has_test: bool = False,
    response_url: str | None = None,
    relations: list[str] | None = None,
) -> dict[str, Any]:
    v: dict[str, Any] = {
        "id": vid,
        "name": name,
        "alternate_url": f"https://hh.ru/vacancy/{vid}",
        "archived": archived,
        "has_test": has_test,
        "employer": {"id": employer_id, "name": "Acme"}
        if employer_id
        else None,
    }
    if response_url is not None:
        v["response_url"] = response_url
    if relations is not None:
        v["relations"] = relations
    return v


def _make_api(
    *,
    resumes: list[dict[str, Any]] | None = None,
    vacancies: list[dict[str, Any]] | None = None,
    full_vacancy: dict[str, Any] | None = None,
    employer: dict[str, Any] | None = None,
) -> MagicMock:
    """Возвращает MagicMock api_client с предзаписанными ответами."""
    api = MagicMock()
    resumes = resumes if resumes is not None else [_resume()]
    api.get.side_effect = _build_router(
        resumes=resumes,
        vacancies=vacancies or [],
        full_vacancy=full_vacancy,
        employer=employer,
    )
    # ``api.post`` is asserted in each test (use case must never POST).
    return api


def _build_router(
    *,
    resumes: list[dict[str, Any]],
    vacancies: list[dict[str, Any]],
    full_vacancy: dict[str, Any] | None,
    employer: dict[str, Any] | None,
) -> Any:
    """Возвращает side_effect для api.get, эмулирующий ответы HH API."""

    def get(endpoint: str, params: Any = None, *args: Any, **kwargs: Any):
        if endpoint == "/resumes/mine":
            return {"items": resumes}
        if endpoint.startswith("/resumes/") and endpoint.endswith(
            "/similar_vacancies"
        ):
            return {
                "items": list(vacancies),
                "found": len(vacancies),
                "pages": 1,
                "page": 0,
                "per_page": 100,
            }
        if endpoint.startswith("/vacancies/"):
            return full_vacancy or {
                "id": int(endpoint.rsplit("/", 1)[-1]),
                "name": "V",
                "description": "Description",
                "employer": {"id": 42, "name": "Acme"},
                "alternate_url": "https://hh.ru/vacancy/1",
                "area": {"id": 1, "name": "Moscow"},
                "salary": {"from": 200000, "to": 300000, "currency": "RUR"},
            }
        if endpoint.startswith("/employers/"):
            return employer or {
                "id": 42,
                "name": "Acme",
                "type": "company",
                "site_url": "",
            }
        if endpoint == "/me":
            return {"first_name": "Ivan", "last_name": "Petrov"}
        return {}

    return get


def _make_session() -> MagicMock:
    return MagicMock()


def _build_use_case(
    storage: StorageFacade,
    api: MagicMock,
    *,
    cover_letter_ai: Any = None,
    vacancy_filter_ai_factory: Any = None,
    test_ai: Any = None,
    relevance_handler: Any = None,
) -> PrepareVacanciesUseCase:
    return PrepareVacanciesUseCase(
        api_client=api,
        session=_make_session(),
        storage=storage,
        cover_letter_ai=cover_letter_ai,
        vacancy_filter_ai_factory=vacancy_filter_ai_factory,
        test_ai=test_ai,
        relevance_handler=relevance_handler,
    )


# ─── 1. Vacancy without test → prepared draft, no POST ─────────────


def test_prepare_one_vacancy_no_test_saves_prepared(
    storage: sqlite3.Connection,
):
    """Без тестов: draft со status='prepared', cover_letter, score, reason.

    api_client.post НЕ вызывается (отклики не отправляются).
    """
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1, has_test=False)],
    )
    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    storage.commit()

    # 1. api_client.post не вызывался ни разу
    api.post.assert_not_called()
    # 2. Статистика
    assert isinstance(result, PrepareVacanciesResult)
    assert result.profiles_processed == 1
    assert result.vacancies_seen == 1
    assert result.prepared == 1
    assert result.rejected == 0
    assert result.skipped == 0
    # 3. Draft сохранён
    saved = facade.application_drafts.get_by_resume_vacancy("r1", 1)
    assert saved is not None
    assert saved.status == "prepared"
    assert saved.search_profile_id == "p1"
    # 4. cover_letter — None, потому что cover_letter_ai не задан
    # (используется шаблон, но force=True создаст письмо через шаблон)
    #   — нам важно, что поле заполнено чем-то осмысленным
    assert saved.cover_letter is not None
    assert saved.cover_letter_status == "generated"
    assert saved.has_test is False
    assert saved.test_status is None
    # 5. full_vacancy_json содержит merged data
    assert saved.full_vacancy_json is not None
    assert saved.full_vacancy_json["id"] == 1
    # 6. Employer сохранён
    saved_emp = facade.employers.find(id=42)
    assert any(saved_emp)


# ─── 2. Vacancy with test → answers saved, test_status='generated' ─


def test_prepare_one_vacancy_with_test_saves_answers(
    storage: sqlite3.Connection,
):
    """Вакансия с has_test: черновик помечен ``test_status='manual_required'``.

    Issue #142: test-answer generation was moved from the prepare
    phase to the application_submit phase (issue #77 VSA). The
    prepare phase now just marks the draft as
    ``test_status='manual_required'`` (or ``generated`` only if a
    dedicated test-handler integration is wired in the VSA slice).
    The legacy ``VacancyTestsService`` shim was removed; the VSA
    :class:`job_bot.application_submit.handlers.test_handler.TestHandler`
    lives in the submit slice and runs in its own pipeline stage.
    This test verifies the prepare-side bookkeeping: the draft is
    saved, ``has_test`` is True, and ``test_status`` reflects the
    manual_required status (no AI-generated answers in the prepare
    phase).
    """
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[
            _vacancy(
                1,
                has_test=True,
                response_url=(
                    "https://hh.ru/applicant/vacancy_response?vacancyId=1"
                ),
            )
        ],
    )

    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))
    storage.commit()

    api.post.assert_not_called()
    assert result.prepared == 1
    # No test answers in the prepare phase (issue #142).
    assert result.test_answers == 0

    saved = facade.application_drafts.get_by_resume_vacancy("r1", 1)
    assert saved is not None
    assert saved.has_test is True
    # ``test_status`` is ``None`` when ``response_url`` is set because
    # the VSA application_prep slice defers test-answer generation to
    # the application_submit slice. The draft is saved with the
    # ``response_url`` so the submit phase can pick it up.
    assert saved.test_status is None
    # ``manual_required`` would only be set when ``has_test=True`` and
    # ``response_url`` is missing (no way to fetch the test
    # automatically). The draft is saved with ``response_url`` so the
    # application_submit phase can pick it up and generate the test
    # answers there.
    # No ``application_test_answers`` rows in the prepare phase
    # (issue #142) — those are written by the application_submit
    # slice. The draft is saved with the ``response_url`` so the
    # submit phase can pick it up and generate the test answers
    # there.


def _to_json(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


# ─── 3. AI rejects → skipped_vacancies reason=ai_rejected ──────────


def test_prepare_one_ai_rejected_saves_skipped(
    storage: sqlite3.Connection,
):
    """AI отклоняет → draft.rejected + skipped_vacancies(ai_rejected).

    Issue #142: the legacy ``RelevanceService`` class-method monkey-
    patching was replaced by injecting a mock VSA
    :class:`RelevanceHandler` via the use case's new ``relevance_handler``
    DI parameter. The mock's ``is_suitable_heavy`` returns a rejected
    :class:`RelevanceResult`, and ``analyze_resume_heavy`` returns a
    string (the cache is in-memory in the VSA handler).
    """
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1", ai_filter_mode="heavy"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1)],
    )

    # Возвращаемый AI-клиент для фильтра должен вернуть rejected.
    rejected_result = RelevanceResult(
        suitable=False,
        relevance_score=15,
        success_probability=10,
        primary_stack=["cobol"],
        reason="wrong stack",
    )
    relevance_handler = MagicMock()
    relevance_handler.is_suitable_heavy = MagicMock(
        return_value=rejected_result
    )
    relevance_handler.is_suitable_light = MagicMock(
        return_value=rejected_result
    )
    relevance_handler.analyze_resume_heavy = MagicMock(return_value="analysis")
    relevance_handler.analyze_resume_light = MagicMock(return_value="analysis")
    relevance_handler._relevance_rules = None

    use_case = _build_use_case(facade, api, relevance_handler=relevance_handler)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))
    storage.commit()

    api.post.assert_not_called()
    assert result.rejected == 1
    assert result.prepared == 0

    # skipped_vacancies с reason='ai_rejected'
    skipped = list(facade.skipped_vacancies.find(reason="ai_rejected"))
    assert len(skipped) == 1
    assert skipped[0].vacancy_id == 1
    assert skipped[0].resume_id == "r1"
    # Draft также сохранён со статусом 'rejected'
    saved = facade.application_drafts.get_by_resume_vacancy("r1", 1)
    assert saved is not None
    assert saved.status == "rejected"


# ─── 4. Duplicate run → single draft, answers upsert ───────────────


def test_duplicate_run_upserts_drafts_and_answers(
    storage: sqlite3.Connection,
):
    """Повторный запуск: один draft, ответы перезаписаны, нет дублей."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    storage.commit()

    test_data_for_vacancy = {
        "uidPk": "u1",
        "guid": "g1",
        "startTime": "t1",
        "required": "true",
        "tasks": [
            {
                "id": "task-1",
                "description": "Q?",
                "candidateSolutions": [
                    {"id": "1", "text": "Yes"},
                    {"id": "2", "text": "No"},
                ],
            }
        ],
    }
    tests_payload = {"1": test_data_for_vacancy}

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[
            _vacancy(
                1,
                has_test=True,
                response_url="https://hh.ru/applicant/vacancy_response?vacancyId=1",
            )
        ],
    )

    use_case = _build_use_case(facade, api)
    session = _make_session()
    session.get.return_value.text = (
        ',"vacancyTests":' + _to_json(tests_payload) + ',"counters":'
    )
    session.get.return_value.status_code = 200
    use_case.session = session

    use_case.execute(PrepareVacanciesCommand(search_profile="p1"))
    use_case.execute(PrepareVacanciesCommand(search_profile="p1"))
    storage.commit()

    # Один draft
    assert facade.application_drafts.count_total() == 1
    # Issue #142: test-answer generation moved to the application_submit
    # phase, so the prepare phase writes no ``application_test_answers``
    # rows. The draft is upserted correctly.
    saved = facade.application_drafts.get_by_resume_vacancy("r1", 1)
    answers = facade.application_test_answers.find_by_draft(saved.id)
    assert answers == []


# ─── 5. Dry-run → no DB writes ─────────────────────────────────────


def test_dry_run_does_not_write_to_db(storage: sqlite3.Connection):
    """dry_run=True: ни одной записи в БД, только вывод."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1), _vacancy(2)],
    )
    use_case = _build_use_case(facade, api)

    # Запоминаем текущие счётчики
    before_drafts = facade.application_drafts.count_total()
    before_answers = facade.application_test_answers.count_total()
    before_skipped = facade.skipped_vacancies.count_total()
    before_vacancies = facade.vacancies.count_total()
    before_employers = facade.employers.find(id=42)
    before_employers = sum(1 for _ in before_employers)
    before_resumes = storage.execute("SELECT COUNT(*) FROM resumes").fetchone()[
        0
    ]

    result = use_case.execute(
        PrepareVacanciesCommand(search_profile="p1", dry_run=True)
    )
    storage.commit()

    # POST не вызывался
    api.post.assert_not_called()
    # Статистика: всё ушло в "prepared" (в dry-run мы их считаем)
    assert result.prepared == 2
    assert result.vacancies_seen == 2
    # Но в БД ничего не появилось
    assert facade.application_drafts.count_total() == before_drafts
    assert facade.application_test_answers.count_total() == before_answers
    assert facade.skipped_vacancies.count_total() == before_skipped
    assert facade.vacancies.count_total() == before_vacancies
    assert sum(1 for _ in facade.employers.find(id=42)) == before_employers
    after_resumes = storage.execute("SELECT COUNT(*) FROM resumes").fetchone()[
        0
    ]
    assert after_resumes == before_resumes


# ─── 6. search-profile filter → only that profile ──────────────────


def test_search_profile_filter_processes_only_one(
    storage: sqlite3.Connection,
):
    """Два включённых профиля, --search-profile=p1 → только p1."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1", resume_id="r1"))
    facade.search_profiles.save(_profile("p2", resume_id="r2"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1"), _resume("r2")],
        vacancies=[_vacancy(1), _vacancy(2)],
    )
    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    assert result.profiles_processed == 1
    # Должны обработать вакансии только для p1 (его resume r1)
    # VacancySearchService.search вызывается один раз (для p1).
    # Найти вызовы:
    search_calls = [
        c
        for c in api.get.call_args_list
        if c.args[0].endswith("/similar_vacancies")
    ]
    assert len(search_calls) == 1


# ─── 7. Disabled profile skipped without explicit id ───────────────


def test_disabled_profile_skipped_by_default(
    storage: sqlite3.Connection,
):
    """Без --search-profile выключенный профиль пропускается."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1", enabled=False))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1)],
    )
    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand())

    assert result.profiles_processed == 0
    # Никаких GET на similar_vacancies
    search_calls = [
        c
        for c in api.get.call_args_list
        if c.args[0].endswith("/similar_vacancies")
    ]
    assert search_calls == []


# ─── 8. Disabled profile still processed when explicit id given ────


def test_disabled_profile_processed_by_explicit_id(
    storage: sqlite3.Connection,
):
    """С --search-profile= выключенный профиль всё равно обрабатывается."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1", enabled=False))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1)],
    )
    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    assert result.profiles_processed == 1
    assert result.prepared == 1


# ─── 9. Skip relations/archived/previously_skipped ─────────────────


def test_skip_relations_vacancy(storage: sqlite3.Connection):
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1, relations=["got_response"])],
    )
    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    assert result.skipped == 1
    assert result.prepared == 0
    assert facade.application_drafts.count_total() == 0


def test_skip_archived_vacancy(storage: sqlite3.Connection):
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1, archived=True)],
    )
    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    assert result.skipped == 1


def test_skip_previously_skipped_vacancy(storage: sqlite3.Connection):
    """Если вакансия уже в skipped_vacancies — пропускаем."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    facade.skipped_vacancies.save(
        {
            "resume_id": "r1",
            "vacancy_id": 1,
            "reason": "ai_rejected",
            "alternate_url": "https://hh.ru/vacancy/1",
            "name": "V",
            "employer_name": "Acme",
        }
    )
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1)],
    )
    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    assert result.skipped == 1
    assert result.prepared == 0


# ─── 10. No resumes → no profiles processed ────────────────────────


def test_no_published_resumes_exits_cleanly(storage: sqlite3.Connection):
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    storage.commit()

    # Есть черновик резюме, но он НЕ published
    api = _make_api(resumes=[_resume("r1", status="not_published")])
    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    assert result.profiles_processed == 0
    assert result.prepared == 0
    # Никаких similar_vacancies
    search_calls = [
        c
        for c in api.get.call_args_list
        if c.args[0].endswith("/similar_vacancies")
    ]
    assert search_calls == []


# ─── 11. Resume missing for profile → profile skipped ──────────────


def test_profile_skipped_when_resume_not_published(
    storage: sqlite3.Connection,
):
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1", resume_id="r-missing"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],  # только r1, не r-missing
        vacancies=[_vacancy(1)],
    )
    use_case = _build_use_case(facade, api)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    assert result.profiles_processed == 1
    # Но вакансии не обрабатываются (не нашли resume)
    assert result.vacancies_seen == 0
    search_calls = [
        c
        for c in api.get.call_args_list
        if c.args[0].endswith("/similar_vacancies")
    ]
    assert search_calls == []


# ─── 12. ai_filter_mode skipped when factory is None ───────────────


def test_ai_filter_skipped_when_factory_none(storage: sqlite3.Connection):
    """ai_filter_mode=heavy, но factory=None → вакансия не rejected."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1", ai_filter_mode="heavy"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1)],
    )
    use_case = _build_use_case(facade, api, vacancy_filter_ai_factory=None)

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    # Прошло как "prepared" (без AI-фильтра, но и не rejected)
    assert result.prepared == 1
    assert result.rejected == 0


# ─── 13. ai_filter_mode=None → вакансия не rejected ────────────────


def test_ai_filter_none_means_no_filtering(storage: sqlite3.Connection):
    """ai_filter_mode=None → вакансия идёт в prepared, не в rejected."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1", ai_filter_mode=None))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1)],
    )
    use_case = _build_use_case(
        facade, api, vacancy_filter_ai_factory=MagicMock()
    )

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    assert result.prepared == 1
    assert result.rejected == 0


# ─── 14. factory raises → still works (logged warning) ─────────────


def test_ai_filter_factory_raises_is_handled(storage: sqlite3.Connection):
    """Если factory падает, use case логирует warning и продолжает."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1", ai_filter_mode="heavy"))
    storage.commit()

    def bad_factory(prompt: str) -> Any:
        raise RuntimeError("ai unavailable")

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1)],
    )
    use_case = _build_use_case(
        facade, api, vacancy_filter_ai_factory=bad_factory
    )

    result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))

    # Без AI-фильтра вакансия просто "prepared"
    assert result.prepared == 1
    assert result.rejected == 0


# ─── 15. Cover letter template path always produces a string ────────


def test_cover_letter_template_path_produces_string(
    storage: sqlite3.Connection,
):
    """Без AI: письмо генерируется через шаблон (force=True)."""
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1", ai_filter_mode=None))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(1)],
    )
    # cover_letter_ai=None → шаблон
    use_case = _build_use_case(facade, api, cover_letter_ai=None)

    use_case.execute(PrepareVacanciesCommand(search_profile="p1"))
    storage.commit()

    saved = facade.application_drafts.get_by_resume_vacancy("r1", 1)
    assert saved is not None
    assert saved.cover_letter_status == "generated"
    assert isinstance(saved.cover_letter, str)
    assert saved.cover_letter


# ─── 16. Cancel event honored ──────────────────────────────────────


def test_cancel_event_stops_iteration(storage: sqlite3.Connection):
    import threading

    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    storage.commit()

    api = _make_api(
        resumes=[_resume("r1")],
        vacancies=[_vacancy(i) for i in range(1, 6)],
    )
    use_case = _build_use_case(facade, api)

    cancel = threading.Event()
    cancel.set()  # уже отменено

    result = use_case.execute(
        PrepareVacanciesCommand(search_profile="p1"),
        cancel_event=cancel,
    )

    # Цикл должен прерваться ДО первой итерации
    assert result.vacancies_seen == 0


# ─── 17. Safety: api.post is NEVER called across full use case run ─


def test_never_calls_api_post_with_test_vacancy(
    storage: sqlite3.Connection,
):
    """Safety test: full use case с тестами + реджектом + обычными — POST не вызывается.

    Issue #142: the legacy ``RelevanceService`` class-method monkey-
    patching was replaced by injecting a mock VSA
    :class:`RelevanceHandler` via the ``relevance_handler`` DI
    parameter. The mock's ``is_suitable_heavy`` returns a per-vacancy
    selective result so one vacancy is rejected and one is approved.
    """
    facade = _make_facade(storage)
    facade.search_profiles.save(_profile("p1"))
    storage.commit()

    # Подменяем relevance через DI.
    rejected_result = RelevanceResult(
        suitable=False,
        relevance_score=5,
        success_probability=5,
        reason="rejected",
    )
    approved_result = RelevanceResult(
        suitable=True,
        relevance_score=90,
        success_probability=80,
        reason="ok",
    )

    def _selective_heavy(vacancy: dict[str, Any]) -> RelevanceResult:
        if vacancy.get("id") == 2:
            return rejected_result
        return approved_result

    relevance_handler = MagicMock()
    relevance_handler.is_suitable_heavy = MagicMock(
        side_effect=_selective_heavy
    )
    relevance_handler.is_suitable_light = MagicMock(
        return_value=approved_result
    )
    relevance_handler.analyze_resume_heavy = MagicMock(return_value="analysis")
    relevance_handler.analyze_resume_light = MagicMock(return_value="analysis")
    relevance_handler._relevance_rules = None

    try:
        api = _make_api(
            resumes=[_resume("r1")],
            vacancies=[
                _vacancy(
                    1,
                    has_test=True,
                    response_url="https://hh.ru/applicant/vacancy_response?vacancyId=1",
                ),
                _vacancy(2),
                _vacancy(3, relations=["got_response"]),  # skipped
                _vacancy(4, archived=True),  # skipped
            ],
        )
        # Только одна вакансия (id=1) идёт через session.get для тестов
        test_payload = {
            "1": {
                "uidPk": "u1",
                "guid": "g1",
                "startTime": "t1",
                "required": "true",
                "tasks": [
                    {
                        "id": "task-1",
                        "description": "Q?",
                        "candidateSolutions": [
                            {"id": "1", "text": "a"},
                            {"id": "2", "text": "b"},
                        ],
                    }
                ],
            }
        }
        session = _make_session()
        session.get.return_value.text = (
            ',"vacancyTests":' + _to_json(test_payload) + ',"counters":'
        )
        session.get.return_value.status_code = 200
        use_case = _build_use_case(
            facade, api, relevance_handler=relevance_handler
        )
        use_case.session = session

        result = use_case.execute(PrepareVacanciesCommand(search_profile="p1"))
        storage.commit()
    finally:
        pass

    # api.post не вызывался НИ РАЗУ
    api.post.assert_not_called()
    # session.post тоже не вызывался
    session.post.assert_not_called()  # type: ignore[attr-defined]
    # Метрики: 4 вакансии, 1 prepared, 1 rejected, 2 skipped
    assert result.vacancies_seen == 4
    assert result.prepared == 1
    assert result.rejected == 1
    assert result.skipped == 2
    # Issue #142: test-answer generation moved to the application_submit
    # phase, so the prepare phase records ``test_answers=0``. The
    # draft itself carries ``response_url`` for the submit phase to
    # pick up.
    assert result.test_answers == 0
