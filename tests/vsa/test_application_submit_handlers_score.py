"""Tests for ScoreHandler (issue #145).

The handler delegates the actual AI calls to
:class:`job_bot.application_prep.handlers.relevance_handler.RelevanceHandler`.
The tests use a fake relevance handler that records calls and returns
pre-baked suitability results.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.application.dto import ApplyToVacanciesCommand
from job_bot.application_submit.handlers.score_handler import ScoreHandler


class _FakeRelevanceResult:
    """Duck-typed stand-in for :class:`RelevanceResult`."""

    def __init__(self, suitable: bool, score: int = 80) -> None:
        self.suitable = suitable
        self.score = score
        self.reason = "fake"
        self.raw_response = "{}"


class _FakeRelevanceHandler:
    """Records calls and returns pre-baked suitability results.

    Args:
        heavy_suitable: value returned by ``is_suitable_heavy``.
        light_suitable: value returned by ``is_suitable_light``.
        resume_analysis: returned by both ``analyze_resume_*``.
    """

    def __init__(
        self,
        *,
        heavy_suitable: bool = True,
        light_suitable: bool = True,
        resume_analysis: str = "fake analysis",
    ) -> None:
        self.heavy_suitable = heavy_suitable
        self.light_suitable = light_suitable
        self.resume_analysis = resume_analysis
        self._relevance_rules: dict[str, Any] = {"rule": "value"}
        self.analyze_heavy_calls: list[dict[str, Any]] = []
        self.analyze_light_calls: list[dict[str, Any]] = []
        self.suitable_heavy_calls: list[dict[str, Any]] = []
        self.suitable_light_calls: list[dict[str, Any]] = []
        self.ai_client: Any = None

    def analyze_resume_heavy(self, resume: dict[str, Any]) -> str:
        self.analyze_heavy_calls.append(resume)
        return self.resume_analysis

    def analyze_resume_light(self, resume: dict[str, Any]) -> str:
        self.analyze_light_calls.append(resume)
        return self.resume_analysis

    def is_suitable_heavy(
        self, vacancy: dict[str, Any]
    ) -> _FakeRelevanceResult:
        self.suitable_heavy_calls.append(vacancy)
        return _FakeRelevanceResult(suitable=self.heavy_suitable)

    def is_suitable_light(
        self, vacancy: dict[str, Any]
    ) -> _FakeRelevanceResult:
        self.suitable_light_calls.append(vacancy)
        return _FakeRelevanceResult(suitable=self.light_suitable)


# ─── init_ai_filter ───────────────────────────────────────────────────


class TestScoreHandlerInitAiFilter:
    """``init_ai_filter`` builds the per-resume AI client and assigns it
    to the relevance handler. Returns the resume_analysis text."""

    def test_no_ai_filter_returns_empty(self) -> None:
        rel = _FakeRelevanceHandler()
        handler = ScoreHandler(rel)
        command = ApplyToVacanciesCommand(ai_filter=None)
        result = handler.init_ai_filter({"id": "r1"}, command)
        assert result == ""
        # Relevance handler was not touched.
        assert rel.analyze_heavy_calls == []
        assert rel.analyze_light_calls == []
        assert rel.ai_client is None

    def test_heavy_ai_filter_calls_analyze_heavy(self) -> None:
        rel = _FakeRelevanceHandler()
        factory = MagicMock(return_value=MagicMock(name="heavy_ai"))
        handler = ScoreHandler(rel, vacancy_filter_ai_factory=factory)
        command = ApplyToVacanciesCommand(ai_filter="heavy", ai_rate_limit=42)
        result = handler.init_ai_filter({"id": "r1"}, command)
        assert result == "fake analysis"
        assert rel.analyze_heavy_calls == [{"id": "r1"}]
        assert rel.analyze_light_calls == []
        # Factory was called and the AI client was assigned.
        assert factory.call_count == 1
        assert rel.ai_client is not None

    def test_light_ai_filter_calls_analyze_light(self) -> None:
        rel = _FakeRelevanceHandler()
        factory = MagicMock(return_value=MagicMock(name="light_ai"))
        handler = ScoreHandler(rel, vacancy_filter_ai_factory=factory)
        command = ApplyToVacanciesCommand(ai_filter="light", ai_rate_limit=42)
        result = handler.init_ai_filter({"id": "r1"}, command)
        assert result == "fake analysis"
        assert rel.analyze_light_calls == [{"id": "r1"}]
        assert rel.analyze_heavy_calls == []

    def test_unknown_ai_filter_raises(self) -> None:
        rel = _FakeRelevanceHandler()
        handler = ScoreHandler(rel)
        command = ApplyToVacanciesCommand(ai_filter="bogus")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Неизвестный режим AI фильтра"):
            handler.init_ai_filter({"id": "r1"}, command)

    def test_no_ai_client_raises_when_filter_enabled(self) -> None:
        """When the filter is enabled but neither ``vacancy_filter_ai``
        nor ``vacancy_filter_ai_factory`` is set, ``init_ai_filter``
        raises a clear ``ValueError``."""
        rel = _FakeRelevanceHandler()
        handler = ScoreHandler(rel)
        command = ApplyToVacanciesCommand(ai_filter="heavy")
        with pytest.raises(ValueError, match="AI фильтр включён"):
            handler.init_ai_filter({"id": "r1"}, command)

    def test_pre_injected_ai_client_is_used(self) -> None:
        """When ``vacancy_filter_ai`` is supplied, no factory call is
        made; the supplied client is assigned to the relevance handler."""
        rel = _FakeRelevanceHandler()
        pre_ai = MagicMock(name="pre_injected_ai")
        handler = ScoreHandler(rel, vacancy_filter_ai=pre_ai)
        command = ApplyToVacanciesCommand(ai_filter="heavy", ai_rate_limit=99)
        handler.init_ai_filter({"id": "r1"}, command)
        assert rel.ai_client is pre_ai
        # The supplied client received the rate limit.
        assert pre_ai.rate_limit == 99

    def test_rate_limit_applied_to_factory_built_ai(self) -> None:
        """When the factory builds the AI client, the rate limit is
        applied to it."""
        rel = _FakeRelevanceHandler()
        factory_ai = MagicMock(name="factory_ai")
        factory = MagicMock(return_value=factory_ai)
        handler = ScoreHandler(rel, vacancy_filter_ai_factory=factory)
        command = ApplyToVacanciesCommand(ai_filter="heavy", ai_rate_limit=77)
        handler.init_ai_filter({"id": "r1"}, command)
        assert factory_ai.rate_limit == 77


# ─── is_suitable ──────────────────────────────────────────────────────


class TestScoreHandlerIsSuitable:
    """``is_suitable`` delegates to the relevance handler."""

    def test_no_ai_filter_returns_true(self) -> None:
        rel = _FakeRelevanceHandler(heavy_suitable=False)
        handler = ScoreHandler(rel)
        command = ApplyToVacanciesCommand(ai_filter=None)
        assert handler.is_suitable({"id": 1}, command) is True
        # Heavy was not called because there's no filter.
        assert rel.suitable_heavy_calls == []

    def test_heavy_delegates_to_relevance_handler(self) -> None:
        rel = _FakeRelevanceHandler(heavy_suitable=False)
        handler = ScoreHandler(rel)
        command = ApplyToVacanciesCommand(ai_filter="heavy")
        vacancy = {"id": 1, "name": "V"}
        assert handler.is_suitable(vacancy, command) is False
        assert rel.suitable_heavy_calls == [vacancy]

    def test_light_delegates_to_relevance_handler(self) -> None:
        rel = _FakeRelevanceHandler(light_suitable=False)
        handler = ScoreHandler(rel)
        command = ApplyToVacanciesCommand(ai_filter="light")
        vacancy = {"id": 1, "name": "V"}
        assert handler.is_suitable(vacancy, command) is False
        assert rel.suitable_light_calls == [vacancy]

    def test_unknown_ai_filter_raises(self) -> None:
        rel = _FakeRelevanceHandler()
        handler = ScoreHandler(rel)
        command = ApplyToVacanciesCommand(ai_filter="bogus")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Неизвестный режим AI фильтра"):
            handler.is_suitable({"id": 1}, command)


# ─── Properties ───────────────────────────────────────────────────────


def test_score_handler_exposes_relevance_handler_and_vacancy_filter_ai() -> (
    None
):
    """The :attr:`relevance_handler` and :attr:`vacancy_filter_ai`
    properties return the same objects passed in / built internally."""
    rel = _FakeRelevanceHandler()
    pre_ai = MagicMock(name="pre_ai")
    handler = ScoreHandler(rel, vacancy_filter_ai=pre_ai)
    assert handler.relevance_handler is rel
    assert handler.vacancy_filter_ai is pre_ai


# ─── Protocol satisfaction ────────────────────────────────────────────


def test_score_handler_satisfies_score_port() -> None:
    from job_bot.application_submit.ports.score_port import ScorePort

    rel = _FakeRelevanceHandler()
    handler: ScorePort = ScoreHandler(rel)
    assert callable(handler.init_ai_filter)
    assert callable(handler.is_suitable)
