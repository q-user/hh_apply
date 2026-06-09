"""Тесты режимов реакции на сбой AI в ``RelevanceService``.

issue #28: при сбое AI сервис может вести себя по-разному:
- ``"permissive"`` (default) — вакансия считается подходящей;
- ``"strict"`` — вакансия отклоняется;
- ``"raise"`` — исключение пробрасывается наверх.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.ai.base import AIError
from hh_applicant_tool.services.relevance import (
    MAX_RETRIES,
    RelevanceService,
)

# ─── Конструктор ───────────────────────────────────────────────


def test_invalid_ai_failure_mode_raises():
    """Неизвестный режим → ValueError в конструкторе."""
    api = MagicMock()
    with pytest.raises(ValueError, match="ai_failure_mode"):
        RelevanceService(api, ai_failure_mode="wrong_mode")


def test_default_ai_failure_mode_is_permissive():
    """По умолчанию ai_failure_mode='permissive'."""
    api = MagicMock()
    svc = RelevanceService(api)
    assert svc._ai_failure_mode == "permissive"


# ─── permissive ─────────────────────────────────────────────────


def test_permissive_ai_error_returns_suitable_true():
    """permissive: AIError → suitable=True (не блокируем)."""
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    ai.complete.side_effect = AIError("rate limit")
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="permissive")

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is True


def test_permissive_max_retries_returns_suitable_true():
    """permissive + max_retries_exceeded → suitable=True."""
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    # Никогда не отдаёт валидный JSON — триггерит max_retries_exceeded
    ai.complete.return_value = "not a json"
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="permissive")

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is True
    assert ai.complete.call_count == MAX_RETRIES


# ─── strict ─────────────────────────────────────────────────────


def test_strict_ai_error_returns_suitable_false():
    """strict: AIError → suitable=False (отклоняем)."""
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    ai.complete.side_effect = AIError("api down")
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="strict")

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is False


def test_strict_max_retries_returns_suitable_false():
    """strict + max_retries_exceeded → suitable=False."""
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    ai.complete.return_value = "not a json"
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="strict")

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is False
    assert ai.complete.call_count == MAX_RETRIES


# ─── raise ──────────────────────────────────────────────────────


def test_raise_ai_error_propagates():
    """raise: AIError пробрасывается наружу."""
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    ai.complete.side_effect = AIError("fatal")
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="raise")

    with pytest.raises(AIError):
        svc.is_suitable_heavy({"id": 1, "name": "X"})


def test_raise_max_retries_raises():
    """raise + max_retries_exceeded → AIError."""
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    ai.complete.return_value = "not a json"
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="raise")

    with pytest.raises(AIError):
        svc.is_suitable_heavy({"id": 1, "name": "X"})
    # Все MAX_RETRIES попыток были использованы
    assert ai.complete.call_count == MAX_RETRIES


# ─── Light-путь ведёт себя так же ──────────────────────────────


def test_strict_ai_error_on_light():
    """strict режим работает и в light-пути (без full vacancy)."""
    api = MagicMock()
    # get_vacancy_key_skills → {"key_skills": [...]}
    api.get.return_value = {"key_skills": [{"name": "Go"}]}
    ai = MagicMock()
    ai.complete.side_effect = AIError("api down")
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="strict")

    r = svc.is_suitable_light({"id": 1, "name": "X"})
    assert r.suitable is False


def test_raise_ai_error_on_light():
    """raise режим работает и в light-пути."""
    api = MagicMock()
    api.get.return_value = {"key_skills": [{"name": "Go"}]}
    ai = MagicMock()
    ai.complete.side_effect = AIError("fatal")
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="raise")

    with pytest.raises(AIError):
        svc.is_suitable_light({"id": 1, "name": "X"})


# ─── Reason попадает в raw_response ────────────────────────────


def test_strict_records_failure_reason():
    """strict: причина сбоя попадает в raw_response / reason."""
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    ai.complete.side_effect = AIError("rate limit exceeded")
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="strict")

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is False
    assert r.raw_response is not None
    assert "rate limit" in r.raw_response


def test_permissive_records_failure_reason():
    """permissive: причина сбоя попадает в raw_response."""
    api = MagicMock()
    api.get.return_value = {"description": "<p>X</p>"}
    ai = MagicMock()
    ai.complete.side_effect = AIError("api timeout")
    svc = RelevanceService(api, ai_client=ai, ai_failure_mode="permissive")

    r = svc.is_suitable_heavy({"id": 1, "name": "X"})
    assert r.suitable is True
    assert r.raw_response is not None
    assert "api timeout" in r.raw_response
