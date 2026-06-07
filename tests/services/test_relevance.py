"""Тесты нового строгого JSON-контракта ``RelevanceService`` (issue #4).

Этот файл фокусируется на требованиях issue #4:

- structured ``RelevanceResult`` (``relevance_score``,
  ``success_probability``, ``primary_stack``, ``risks`` и т.д.);
- строгие system prompt'ы с обязательными полями;
- ``relevance_rules`` (issue #4): ``min_score``, ``reject_if_primary``,
  ``allowed_secondary``, ``must_have`` и т.д.

Базовые сценарии (boolean-only ответ, retry на невалидный JSON, etc.)
остались в ``tests/test_services_relevance.py`` — здесь только то, что
относится к расширенному контракту.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from hh_applicant_tool.services.relevance import (
    SCORE_MAX,
    SCORE_MIN,
    RelevanceResult,
    RelevanceService,
    _apply_relevance_rules,
    _as_str_list,
    build_filter_system_prompt_heavy,
    build_filter_system_prompt_light,
    parse_ai_json_response,
)


class FakeAI:
    """Простейший fake ``ai_client``.

    Поддерживает заранее заданную очередь ответов. Удобен для
    сценариев «сначала мусор, потом валидный JSON» (retry-логика).
    """

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.responses.pop(0)


# ─── 1. parse_ai_json_response — boolean-only ────────────────────


def test_parse_yes_returns_suitable_true_no_score():
    r = parse_ai_json_response("да")
    assert r == RelevanceResult(suitable=True, raw_response="да")
    assert r.relevance_score is None  # score не заполняется
    assert r.reason is None


def test_parse_no_returns_suitable_false_no_score():
    r = parse_ai_json_response("нет")
    assert r == RelevanceResult(suitable=False, raw_response="нет")
    assert r.relevance_score is None


def test_parse_yes_case_insensitive():
    r = parse_ai_json_response("YES")
    assert r is not None
    assert r.suitable is True
    # raw_response сохраняется как есть (для дебага)
    assert r.raw_response == "YES"


# ─── 2. parse_ai_json_response — строгий JSON со всеми полями ──────


def test_parse_strict_json_with_all_fields():
    raw = json.dumps(
        {
            "suitable": True,
            "relevance_score": 92,
            "success_probability": 78,
            "primary_stack": ["Python", "Django", "PostgreSQL"],
            "secondary_stack": ["FastAPI"],
            "project_summary": "Legacy Django backend",
            "complexity": "medium",
            "salary_summary": "250 000 ₽",
            "employment_format": "remote, full-time",
            "perks": ["remote", "white salary"],
            "risks": ["FastAPI mentioned as secondary stack"],
            "reason": "Primary stack is Django, matches candidate experience",
        }
    )
    r = parse_ai_json_response(raw)
    assert r is not None
    assert r.suitable is True
    assert r.relevance_score == 92
    assert r.success_probability == 78
    assert r.primary_stack == ["Python", "Django", "PostgreSQL"]
    assert r.secondary_stack == ["FastAPI"]
    assert r.project_summary == "Legacy Django backend"
    assert r.complexity == "medium"
    assert r.salary_summary == "250 000 ₽"
    assert r.employment_format == "remote, full-time"
    assert r.perks == ["remote", "white salary"]
    assert r.risks == ["FastAPI mentioned as secondary stack"]
    assert r.reason == ("Primary stack is Django, matches candidate experience")
    assert r.raw_response == raw


def test_parse_strict_json_missing_fields_kept_none():
    r = parse_ai_json_response(
        '{"suitable": true, "relevance_score": 80, "reason": "ok"}'
    )
    assert r is not None
    assert r.suitable is True
    assert r.relevance_score == 80
    assert r.success_probability is None
    assert r.primary_stack is None
    assert r.secondary_stack is None
    assert r.risks is None


# ─── 3. parse_ai_json_response — markdown fence ───────────────────


def test_parse_strict_json_in_markdown_fence():
    raw = (
        "```json\n"
        '{"suitable": true, "relevance_score": 85, '
        '"primary_stack": ["Python", "Django"]}\n'
        "```"
    )
    r = parse_ai_json_response(raw)
    assert r is not None
    assert r.suitable is True
    assert r.relevance_score == 85
    assert r.primary_stack == ["Python", "Django"]


def test_parse_markdown_fence_without_language():
    """AI иногда ставит ``` без ``json`` — должно работать."""
    raw = (
        "```\n"
        '{"suitable": false, "relevance_score": 30, '
        '"reason": "wrong stack"}\n'
        "```"
    )
    r = parse_ai_json_response(raw)
    assert r is not None
    assert r.suitable is False
    assert r.relevance_score == 30


# ─── 4. parse_ai_json_response — legacy JSON ──────────────────────


def test_parse_legacy_score_alias_to_relevance_score():
    """Legacy ``score`` трактуется как alias ``relevance_score``."""
    r = parse_ai_json_response(
        '{"suitable": true, "score": 75, "reason": "ok"}'
    )
    assert r is not None
    assert r.suitable is True
    assert r.relevance_score == 75
    # back-compat: .score всё ещё работает
    assert r.score == 75


def test_parse_relevance_score_takes_precedence_over_legacy_score():
    """Если AI вернул оба — ``relevance_score`` побеждает."""
    r = parse_ai_json_response(
        '{"suitable": true, "score": 50, "relevance_score": 90}'
    )
    assert r is not None
    assert r.relevance_score == 90
    assert r.score == 90


def test_parse_legacy_minimal_json():
    """Самый минимальный legacy-формат: ``{"suitable": true, "reason": "..."}``."""
    r = parse_ai_json_response('{"suitable": true, "reason": "match"}')
    assert r is not None
    assert r.suitable is True
    assert r.relevance_score is None
    assert r.reason == "match"


# ─── 5. parse_ai_json_response — malformed → None ─────────────────


def test_parse_malformed_json_returns_none():
    assert parse_ai_json_response("not a json") is None
    assert parse_ai_json_response("42") is None
    assert parse_ai_json_response("hello world") is None
    assert parse_ai_json_response("{") is None
    assert parse_ai_json_response("[1, 2, 3]") is None  # не dict
    assert parse_ai_json_response('{"foo": "bar"}') is None  # нет suitable


def test_parse_empty_returns_none():
    assert parse_ai_json_response("") is None
    assert parse_ai_json_response("   ") is None
    assert parse_ai_json_response(None) is None


def test_parse_does_not_raise_on_garbage():
    """Парсер не должен бросать исключения наружу.

    Для полностью невалидного ввода возвращается ``None`` (для retry).
    Для ввода, который Python ``json.loads`` всё-таки принимает (например,
    ``NaN``), парсер возвращает ``RelevanceResult`` с приведёнными
    значениями (``relevance_score=None`` для NaN).
    """
    # Полностью невалидный JSON → ``None``.
    none_inputs = [
        "{" + "x" * 1000,  # невалидный JSON
        '{"suitable": tru}',  # опечатка
        "```\n```",  # пустой fence
    ]
    for bad in none_inputs:
        result = parse_ai_json_response(bad)  # не raise
        assert result is None

    # ``NaN`` — невалидное число по спеке, но ``json.loads`` его пропускает.
    # Парсер должен вернуть ``RelevanceResult`` с ``relevance_score=None``,
    # ``suitable=True`` (без падения).
    nan_result = parse_ai_json_response(
        '{"suitable": true, "relevance_score": NaN}'
    )
    assert nan_result is not None
    assert nan_result.suitable is True
    assert nan_result.relevance_score is None


# ─── 6. score clamp ──────────────────────────────────────────────


def test_score_clamp_high():
    """``150`` → ``100`` (SCORE_MAX)."""
    r = parse_ai_json_response('{"suitable": true, "relevance_score": 150}')
    assert r is not None
    assert r.relevance_score == SCORE_MAX == 100


def test_score_clamp_low():
    """``-5`` → ``0`` (SCORE_MIN)."""
    r = parse_ai_json_response('{"suitable": true, "relevance_score": -5}')
    assert r is not None
    assert r.relevance_score == SCORE_MIN == 0


def test_score_in_range_unchanged():
    r = parse_ai_json_response('{"suitable": true, "relevance_score": 50}')
    assert r is not None
    assert r.relevance_score == 50


def test_score_float_clamp():
    """``80.7`` → ``80`` (int truncation)."""
    r = parse_ai_json_response('{"suitable": true, "relevance_score": 80.7}')
    assert r is not None
    assert r.relevance_score == 80


def test_score_string_numeric():
    """AI иногда возвращает score строкой — парсится."""
    r = parse_ai_json_response('{"suitable": true, "relevance_score": "85"}')
    assert r is not None
    assert r.relevance_score == 85


def test_score_string_invalid_keeps_none():
    r = parse_ai_json_response('{"suitable": true, "relevance_score": "abc"}')
    assert r is not None
    assert r.relevance_score is None


def test_score_bool_returns_none():
    """``True``/``False`` для score — бессмысленно, не принимаем."""
    r = parse_ai_json_response('{"suitable": true, "relevance_score": true}')
    assert r is not None
    assert r.relevance_score is None


# ─── 7. list-coercion helpers ─────────────────────────────────────


def test_str_list_from_list():
    assert _as_str_list(["Django", "PostgreSQL"]) == [
        "Django",
        "PostgreSQL",
    ]


def test_str_list_filters_empty_and_nulls():
    assert _as_str_list(["Django", "", None, "PG"]) == ["Django", "PG"]


def test_str_list_from_str():
    assert _as_str_list("Django") == ["Django"]


def test_str_list_from_empty_str_returns_none():
    assert _as_str_list("") is None
    assert _as_str_list("   ") is None


def test_str_list_from_non_list_returns_none():
    assert _as_str_list(42) is None
    assert _as_str_list({"Django"}) is None


def test_str_list_int_items_coerced_to_str():
    # AI иногда возвращает числа в стеке — должны стать строками.
    assert _as_str_list([1, 2, "Django"]) == ["1", "2", "Django"]


# ─── 8. to_analysis_json — сериализация для storage ──────────────


def test_to_analysis_json_includes_set_fields_only():
    r = RelevanceResult(
        suitable=True,
        relevance_score=92,
        primary_stack=["Python", "Django"],
        risks=["FastAPI mentioned"],
        reason="match",
    )
    out = r.to_analysis_json()
    assert out == {
        "suitable": True,
        "relevance_score": 92,
        "primary_stack": ["Python", "Django"],
        "risks": ["FastAPI mentioned"],
        "reason": "match",
    }


def test_to_analysis_json_omits_none_fields():
    r = RelevanceResult(suitable=False)
    out = r.to_analysis_json()
    assert out == {"suitable": False}


def test_to_analysis_json_excludes_raw_response_and_applied_rules():
    r = RelevanceResult(
        suitable=True,
        relevance_score=80,
        raw_response="very long raw text",
        applied_rules={"min_score": 80},
    )
    out = r.to_analysis_json()
    assert "raw_response" not in out
    assert "applied_rules" not in out
    assert out["relevance_score"] == 80


# ─── 9. relevance_rules — min_score ──────────────────────────────


def test_min_score_rejects_result_below_threshold():
    """AI вернул ``suitable=true, relevance_score=70``, ``min_score=80``
    → ``suitable=False``, reason дополнен."""
    ai = FakeAI(
        [
            json.dumps(
                {
                    "suitable": True,
                    "relevance_score": 70,
                    "reason": "AI thinks it's ok",
                }
            )
        ]
    )
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    svc = RelevanceService(
        api_client=api,
        ai_client=ai,
        relevance_rules={"min_score": 80},
    )

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})

    assert r.suitable is False
    assert r.relevance_score == 70  # не меняем значение
    assert r.reason is not None
    assert "min_score=80" in r.reason
    assert r.applied_rules.get("min_score") == 80


def test_min_score_does_not_reject_above_threshold():
    ai = FakeAI(
        [json.dumps({"suitable": True, "relevance_score": 85, "reason": "ok"})]
    )
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    svc = RelevanceService(
        api_client=api,
        ai_client=ai,
        relevance_rules={"min_score": 80},
    )

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})

    assert r.suitable is True
    assert r.relevance_score == 85
    assert r.applied_rules == {}


def test_min_score_appends_to_existing_reason():
    ai = FakeAI(
        [
            json.dumps(
                {
                    "suitable": True,
                    "relevance_score": 60,
                    "reason": "AI thinks it's ok",
                }
            )
        ]
    )
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    svc = RelevanceService(
        api_client=api,
        ai_client=ai,
        relevance_rules={"min_score": 80},
    )

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})

    assert r.suitable is False
    assert r.reason is not None
    # Оригинальная причина + добавленная
    assert "AI thinks it's ok" in r.reason
    assert "min_score=80" in r.reason
    # Не дублируется, если AI вдруг вернул ту же формулировку
    assert r.reason.count("min_score=80") == 1


# ─── 10. relevance_rules — reject_if_primary ──────────────────────


def test_reject_if_primary_rejects_when_matched():
    """AI вернул FastAPI в primary_stack, ``reject_if_primary=["FastAPI"]`` → reject."""
    ai = FakeAI(
        [
            json.dumps(
                {
                    "suitable": True,
                    "relevance_score": 90,
                    "primary_stack": ["FastAPI", "PostgreSQL"],
                    "reason": "matches stack",
                }
            )
        ]
    )
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    svc = RelevanceService(
        api_client=api,
        ai_client=ai,
        relevance_rules={"reject_if_primary": ["FastAPI", "Go", "Java"]},
    )

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})

    assert r.suitable is False
    assert r.reason is not None
    assert "FastAPI" in r.reason
    assert r.applied_rules.get("reject_if_primary_matched") == ["FastAPI"]


def test_reject_if_primary_case_insensitive():
    """AI вернул ``fastapi``, правило ``FastAPI`` — должно сматчиться."""
    ai = FakeAI(
        [
            json.dumps(
                {
                    "suitable": True,
                    "relevance_score": 90,
                    "primary_stack": ["fastapi"],
                    "reason": "matches",
                }
            )
        ]
    )
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    svc = RelevanceService(
        api_client=api,
        ai_client=ai,
        relevance_rules={"reject_if_primary": ["FastAPI"]},
    )

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is False


def test_reject_if_primary_no_match_keeps_suitable():
    ai = FakeAI(
        [
            json.dumps(
                {
                    "suitable": True,
                    "relevance_score": 90,
                    "primary_stack": ["Python", "Django"],
                    "reason": "matches",
                }
            )
        ]
    )
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    svc = RelevanceService(
        api_client=api,
        ai_client=ai,
        relevance_rules={"reject_if_primary": ["FastAPI", "Go", "Java"]},
    )

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is True
    assert r.applied_rules == {}


# ─── 11. relevance_rules — allowed_secondary ──────────────────────


def test_allowed_secondary_does_not_reject():
    """``allowed_secondary`` — НЕ должен отклонять вакансию,
    если технология во ``secondary_stack``.

    Это «разрешающее» правило: оно сообщает AI в system prompt, а
    сервис его никак не штрафует. Здесь проверяем, что вакансия с
    FastAPI в ``secondary_stack`` остаётся ``suitable=True`` (AI сам
    решил, что FastAPI ОК как вторичный стек).
    """
    ai = FakeAI(
        [
            json.dumps(
                {
                    "suitable": True,
                    "relevance_score": 88,
                    "primary_stack": ["Python", "Django"],
                    "secondary_stack": ["FastAPI"],
                    "reason": "Primary stack is Django, FastAPI is OK secondary",
                }
            )
        ]
    )
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    svc = RelevanceService(
        api_client=api,
        ai_client=ai,
        relevance_rules={"allowed_secondary": ["FastAPI"]},
    )

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})

    # allowed_secondary никак не наказывает вторичный стек.
    assert r.suitable is True
    assert r.relevance_score == 88
    # applied_rules пуст — это правило действует только через prompt.
    assert r.applied_rules == {}


def test_allowed_secondary_in_system_prompt():
    """``allowed_secondary`` (и другие правила) попадают в system prompt."""
    rules = {
        "must_have": ["Django"],
        "nice_to_have": ["PostgreSQL"],
        "allowed_secondary": ["FastAPI"],
        "reject_if_primary": ["Go", "Java"],
        "strict_role": "backend python django developer",
        "min_score": 80,
    }
    heavy = build_filter_system_prompt_heavy("RESUME", relevance_rules=rules)
    light = build_filter_system_prompt_light("RESUME", relevance_rules=rules)

    # Правила попадают в оба промпта.
    for prompt in (heavy, light):
        assert "Django" in prompt
        assert "PostgreSQL" in prompt
        assert "FastAPI" in prompt
        assert "Go" in prompt
        assert "Java" in prompt
        assert "backend python django developer" in prompt
        assert "ПРАВИЛА РЕЛЕВАНТНОСТИ" in prompt


# ─── 12. _apply_relevance_rules — unit-тест helper'а ─────────────


def test_apply_relevance_rules_with_no_rules_is_noop():
    r = RelevanceResult(suitable=True, relevance_score=70)
    _apply_relevance_rules(r, None)
    assert r.suitable is True
    assert r.applied_rules == {}


def test_apply_relevance_rules_combines_min_score_and_reject():
    """Сразу два правила сработали → обе ноты в reason, suitable=False."""
    r = RelevanceResult(
        suitable=True,
        relevance_score=50,
        primary_stack=["Go"],
        reason="AI was optimistic",
    )
    _apply_relevance_rules(
        r,
        {
            "min_score": 80,
            "reject_if_primary": ["Go"],
        },
    )
    assert r.suitable is False
    assert r.reason is not None
    assert "AI was optimistic" in r.reason
    assert "min_score=80" in r.reason
    assert "Go" in r.reason
    assert r.applied_rules.get("min_score") == 80
    assert r.applied_rules.get("reject_if_primary_matched") == ["Go"]


# ─── 13. System prompts — обязательные поля нового контракта ─────


def test_heavy_prompt_requires_new_contract_fields():
    p = build_filter_system_prompt_heavy("RESUME")
    # Строгий JSON с новыми полями (issue #4).
    for field in (
        "relevance_score",
        "success_probability",
        "primary_stack",
        "secondary_stack",
        "project_summary",
        "complexity",
        "salary_summary",
        "employment_format",
        "perks",
        "risks",
        "reason",
    ):
        assert field in p, f"heavy prompt missing field: {field}"
    # Старое ``score`` НЕ должно быть primary-полем — только как alias
    # в парсере. В промпте — ``relevance_score``.
    assert "relevance_score" in p


def test_light_prompt_requires_short_contract_fields():
    p = build_filter_system_prompt_light("RESUME")
    for field in (
        "suitable",
        "relevance_score",
        "primary_stack",
        "secondary_stack",
        "risks",
        "reason",
    ):
        assert field in p, f"light prompt missing field: {field}"


def test_heavy_prompt_resume_placeholder():
    p = build_filter_system_prompt_heavy("MY_RESUME_DATA")
    assert "MY_RESUME_DATA" in p


def test_light_prompt_resume_placeholder():
    p = build_filter_system_prompt_light("MY_RESUME_DATA")
    assert "MY_RESUME_DATA" in p


# ─── 14. Конструктор сервиса — relevance_rules ───────────────────


def test_relevance_service_default_no_rules():
    """Без явного ``relevance_rules`` — никаких ограничений."""
    svc = RelevanceService(api_client=MagicMock(), ai_client=None)
    assert svc.relevance_rules is None


def test_relevance_service_accepts_relevance_rules():
    rules: dict[str, Any] = {
        "must_have": ["Django"],
        "min_score": 80,
    }
    svc = RelevanceService(
        api_client=MagicMock(), ai_client=None, relevance_rules=rules
    )
    assert svc.relevance_rules is rules
