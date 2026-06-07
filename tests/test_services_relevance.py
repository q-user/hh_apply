"""Тесты сервиса AI-фильтра релевантности (issue #3)."""

from __future__ import annotations

from unittest.mock import MagicMock

from hh_applicant_tool.ai.base import AIError
from hh_applicant_tool.services.relevance import (
    MAX_RETRIES,
    RelevanceResult,
    RelevanceService,
    build_filter_system_prompt_heavy,
    build_filter_system_prompt_light,
    parse_ai_json_response,
)

# ─── parse_ai_json_response ─────────────────────────────────────────


def test_parse_yes_no():
    """Базовые «да/нет» ответы."""
    assert parse_ai_json_response("да") == RelevanceResult(
        suitable=True, raw_response="да"
    )
    assert parse_ai_json_response("yes") == RelevanceResult(
        suitable=True, raw_response="yes"
    )
    assert parse_ai_json_response("true") == RelevanceResult(
        suitable=True, raw_response="true"
    )
    assert parse_ai_json_response("нет") == RelevanceResult(
        suitable=False, raw_response="нет"
    )
    assert parse_ai_json_response("no") == RelevanceResult(
        suitable=False, raw_response="no"
    )
    assert parse_ai_json_response("false") == RelevanceResult(
        suitable=False, raw_response="false"
    )


def test_parse_yes_no_case_insensitive():
    assert parse_ai_json_response("ДА") == RelevanceResult(
        suitable=True, raw_response="ДА"
    )
    assert parse_ai_json_response("Yes") == RelevanceResult(
        suitable=True, raw_response="Yes"
    )
    assert parse_ai_json_response("False") == RelevanceResult(
        suitable=False, raw_response="False"
    )


def test_parse_plain_json():
    r = parse_ai_json_response(
        '{"suitable": true, "score": 85, "reason": "match"}'
    )
    assert r is not None
    assert r.suitable is True
    assert r.score == 85
    assert r.reason == "match"
    assert (
        r.raw_response == '{"suitable": true, "score": 85, "reason": "match"}'
    )


def test_parse_plain_json_false():
    r = parse_ai_json_response('{"suitable": false, "reason": "wrong stack"}')
    assert r is not None
    assert r.suitable is False
    assert r.reason == "wrong stack"
    assert r.score is None


def test_parse_fenced_json():
    r = parse_ai_json_response('```json\n{"suitable":false}\n```')
    assert r is not None
    assert r.suitable is False


def test_parse_fenced_json_with_text():
    r = parse_ai_json_response(
        'Вот ответ:\n```json\n{"suitable": true, "score": 90}\n```\nГотово.'
    )
    assert r is not None
    assert r.suitable is True
    assert r.score == 90


def test_parse_fallback_regex():
    text = (
        "Some preface text "
        '{"suitable": true, "score": 75, "reason": "ok"}'
        " trailing text"
    )
    r = parse_ai_json_response(text)
    assert r is not None
    assert r.suitable is True
    assert r.score == 75
    assert r.reason == "ok"


def test_parse_returns_none_on_garbage():
    assert parse_ai_json_response("not a json") is None
    assert parse_ai_json_response("42") is None
    assert parse_ai_json_response("hello world") is None


def test_parse_empty():
    assert parse_ai_json_response("") is None
    assert parse_ai_json_response("   ") is None
    assert parse_ai_json_response(None) is None


def test_parse_invalid_score_keeps_none():
    r = parse_ai_json_response('{"suitable": true, "score": "abc"}')
    assert r is not None
    assert r.suitable is True
    assert r.score is None


def test_parse_score_int_coercion():
    r = parse_ai_json_response('{"suitable": true, "score": 80}')
    assert r is not None
    assert r.score == 80


# ─── System prompts ─────────────────────────────────────────────────


def test_build_filter_system_prompts_contain_resume_analysis():
    heavy = build_filter_system_prompt_heavy("my resume analysis")
    light = build_filter_system_prompt_light("my resume analysis")
    assert "my resume analysis" in heavy
    assert "my resume analysis" in light


def test_build_filter_system_prompt_heavy_has_score_field():
    prompt = build_filter_system_prompt_heavy("X")
    # Legacy-формат: просим score+reason
    assert "score" in prompt
    assert "suitable" in prompt


# ─── RelevanceService ──────────────────────────────────────────────


def test_relevance_service_no_ai_returns_suitable():
    """Без ai_client фильтр выключен — все вакансии подходят."""
    api = MagicMock()
    # Чтобы heavy-путь не упал на strip_tags, отдаём валидный dict
    api.get.return_value = {"description": "<p>X</p>"}
    svc = RelevanceService(api_client=api)  # ai_client=None
    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is True
    assert r.score is None
    assert r.reason is None


def test_relevance_service_heavy_calls_api():
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    ai.complete.return_value = '{"suitable": true, "score": 80, "reason": "ok"}'
    svc = RelevanceService(api, ai_client=ai)

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})

    assert r.suitable is True
    assert r.score == 80
    assert r.reason == "ok"
    api.get.assert_called_with("/vacancies/1")
    ai.complete.assert_called_once()


def test_relevance_service_light_no_full_vacancy():
    """Light не подгружает полное описание — только key_skills."""
    api = MagicMock()
    api.get.return_value = {"key_skills": [{"name": "Go"}]}
    ai = MagicMock()
    ai.complete.return_value = '{"suitable": true}'
    svc = RelevanceService(api, ai_client=ai)

    r = svc.is_suitable_light({"id": 1, "name": "Backend"})

    assert r.suitable is True
    # Промпт содержит key_skills, но не description
    prompt = ai.complete.call_args[0][0]
    assert "Go" in prompt  # key_skills загружен
    assert "description" not in prompt.lower() or "Описание" not in prompt


def test_relevance_service_analyze_resume_heavy_caches():
    api = MagicMock()
    api.get.return_value = {
        "title": "X",
        "skill_set": ["Python"],
        "experience": [],
    }
    svc = RelevanceService(api, ai_client=None)

    out1 = svc.analyze_resume_heavy({"id": "r1"})
    out2 = svc.analyze_resume_heavy({"id": "r1"})

    assert out1 == out2
    assert "Python" in out1
    # API зовётся лишь раз — второй вызов из кеша
    api.get.assert_called_once_with("/resumes/r1")


def test_relevance_service_analyze_resume_light_caches():
    api = MagicMock()
    api.get.return_value = {
        "title": "Backend",
        "skill_set": ["Go"],
    }
    svc = RelevanceService(api, ai_client=None)

    out1 = svc.analyze_resume_light({"id": "r1"})
    out2 = svc.analyze_resume_light({"id": "r1"})

    assert out1 == out2
    assert "Backend" in out1
    assert "Go" in out1
    api.get.assert_called_once()


def test_relevance_service_analyze_resume_heavy_handles_error():
    api = MagicMock()
    api.get.side_effect = RuntimeError("boom")
    svc = RelevanceService(api, ai_client=None)

    out = svc.analyze_resume_heavy({"id": "r1"})

    assert out == ""


def test_relevance_service_analyze_resume_light_handles_error():
    api = MagicMock()
    api.get.side_effect = RuntimeError("boom")
    svc = RelevanceService(api, ai_client=None)

    out = svc.analyze_resume_light({"id": "r1"})

    assert out == ""


def test_relevance_service_analyze_resume_heavy_no_id_returns_empty():
    """Без resume_id — никаких API-вызовов, возврат ''."""
    api = MagicMock()
    svc = RelevanceService(api, ai_client=None)

    out = svc.analyze_resume_heavy({})

    assert out == ""
    api.get.assert_not_called()


def test_relevance_service_ai_error_falls_through_suitable():
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    ai.complete.side_effect = AIError("rate limit")
    svc = RelevanceService(api, ai_client=ai)

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})

    assert r.suitable is True
    assert r.raw_response is not None and "rate limit" in r.raw_response


def test_relevance_service_retries_invalid_json():
    api = MagicMock()
    api.get.return_value = {"description": "desc"}
    ai = MagicMock()
    ai.complete.side_effect = ["not json", '{"suitable": true}']
    svc = RelevanceService(api, ai_client=ai)

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})

    assert r.suitable is True
    assert ai.complete.call_count == 2


def test_relevance_service_gives_up_after_max_retries():
    api = MagicMock()
    api.get.return_value = {"description": "desc"}
    ai = MagicMock()
    ai.complete.return_value = "garbage"  # никогда не парсится
    svc = RelevanceService(api, ai_client=ai)

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})

    # После исчерпания попыток — suitable=True (fallback, не блокировать)
    assert r.suitable is True
    assert ai.complete.call_count == MAX_RETRIES


def test_relevance_service_get_vacancy_key_skills():
    api = MagicMock()
    api.get.return_value = {
        "key_skills": [
            {"name": "Python"},
            {"name": "Django"},
            {"name": ""},
        ]
    }
    svc = RelevanceService(api, ai_client=None)

    out = svc.get_vacancy_key_skills(42)

    assert "Python" in out
    assert "Django" in out
    api.get.assert_called_with("/vacancies/42")


def test_relevance_service_get_vacancy_key_skills_handles_error():
    api = MagicMock()
    api.get.side_effect = RuntimeError("boom")
    svc = RelevanceService(api, ai_client=None)

    out = svc.get_vacancy_key_skills(42)

    assert out == ""


def test_relevance_service_build_vacancy_context_includes_name():
    api = MagicMock()
    svc = RelevanceService(api, ai_client=None)

    out = svc.build_vacancy_context({"id": 1, "name": "Python Developer"})

    assert "Python Developer" in out


def test_relevance_service_build_vacancy_context_includes_description_heavy():
    """include_full=True + full_vacancy вставляет описание."""
    api = MagicMock()
    svc = RelevanceService(api, ai_client=None)

    out = svc.build_vacancy_context(
        {"id": 1, "name": "X"},
        full_vacancy={"description": "<p>Some description</p>"},
        include_full=True,
    )

    assert "Some description" in out


def test_relevance_service_build_vacancy_context_light_uses_key_skills():
    """include_full=False без full_vacancy подгружает key_skills."""
    api = MagicMock()
    api.get.return_value = {"key_skills": [{"name": "Go"}, {"name": "Kafka"}]}
    svc = RelevanceService(api, ai_client=None)

    out = svc.build_vacancy_context(
        {"id": 1, "name": "X"},
        full_vacancy=None,
        include_full=False,
    )

    assert "Go" in out
    assert "Kafka" in out


def test_relevance_service_is_suitable_heavy_uses_full_vacancy():
    """Heavy получает описание из /vacancies/{id} и кладёт его в промпт."""
    api = MagicMock()
    api.get.return_value = {
        "description": "<p>Detailed job description</p>",
    }
    ai = MagicMock()
    ai.complete.return_value = '{"suitable": true}'
    svc = RelevanceService(api, ai_client=ai)

    svc.is_suitable_heavy({"id": 99, "name": "X"})

    api.get.assert_called_with("/vacancies/99")
    prompt = ai.complete.call_args[0][0]
    assert "Detailed job description" in prompt


def test_relevance_service_handles_missing_vacancy_id_heavy():
    """Без vacancy.id не должно быть HTTP-вызова /vacancies/None."""
    api = MagicMock()
    ai = MagicMock()
    ai.complete.return_value = '{"suitable": true}'
    svc = RelevanceService(api, ai_client=ai)

    r = svc.is_suitable_heavy({"name": "X"})

    assert r.suitable is True
    # /vacancies/None не должен вызываться
    for call in api.get.call_args_list:
        assert "/vacancies/None" not in str(call)


# ─── Поведение при отсутствии AI-клиента в pipeline ────────────────


def test_is_suitable_heavy_pipeline_no_ai():
    """Без ai_client pipeline возвращает suitable=True (фильтр выкл)."""
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    svc = RelevanceService(api, ai_client=None)
    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is True
    # В heavy пути /vacancies/1 всё равно дёргается для описания
    api.get.assert_called_with("/vacancies/1")


# ─── Smoke-тест MAX_RETRIES ─────────────────────────────────────────


def test_max_retries_is_3():
    """Защита от случайного изменения константы — поведение зависит от неё."""
    assert MAX_RETRIES == 3
