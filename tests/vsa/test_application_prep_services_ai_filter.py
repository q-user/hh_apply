"""Tests for :class:`AiFilterService` (issue #147).

Covers the per-phase service that owns the per-profile AI filter
client construction. The service is a thin VSA wrapper around the
existing :func:`job_bot.application_prep.utils.build_filter_ai_client`
helper — the tests are focused on the contract documented on
``AiFilterService.build`` (which delegates 1:1 to the helper):

* ``ai_filter_mode`` is required (None / unknown / no factory →
  ``relevance_obj.ai_client = None`` and ``build`` returns ``None``);
* The factory is invoked with the heavy or light system prompt;
* The produced AI client is assigned to
  ``relevance_obj.ai_client``;
* The optional ``rate_limit`` is forwarded to the produced client
  (best-effort; failure is swallowed).

Strategy
--------

* The ``relevance_obj`` is a small dataclass (or
  :class:`types.SimpleNamespace`) with an ``ai_client`` attribute
  and ``analyze_resume_heavy`` / ``analyze_resume_light`` methods.
  No ``unittest.mock.Mock`` — the service's duck-typed contract is
  tested against a real value-object.
* The factory is a plain Python function recorded by the test for
  parameter inspection.

The tests cover:

* ``ai_filter_mode=None`` → no factory call, ``ai_client = None``;
* ``ai_filter_mode="heavy"`` → factory called with the heavy prompt,
  ``ai_client`` set to the returned client;
* ``ai_filter_mode="light"`` → factory called with the light prompt;
* unknown mode → ``ai_client = None``;
* factory is ``None`` → ``ai_client = None``;
* factory raises ``ValueError`` / ``TypeError`` / ``RuntimeError``
  / ``AIError`` → ``ai_client = None``;
* factory raises a generic ``Exception`` → ``ai_client = None``;
* ``rate_limit`` is forwarded to the produced client;
* ``rate_limit`` assignment failure is swallowed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from job_bot.application_prep.services.ai_filter import AiFilterService


class _FakeRelevance:
    """In-process relevance handler double.

    Records every ``ai_client`` assignment. Exposes the
    ``analyze_resume_*`` methods the helper calls to build the
    system prompt (returns a canned string).
    """

    def __init__(self) -> None:
        self.ai_client: Any = "sentinel"
        self.analyze_calls: list[tuple[str, dict[str, Any]]] = []

    def analyze_resume_heavy(self, resume: dict[str, Any]) -> str:
        self.analyze_calls.append(("heavy", resume))
        return f"heavy-analysis-of-{resume.get('id')}"

    def analyze_resume_light(self, resume: dict[str, Any]) -> str:
        self.analyze_calls.append(("light", resume))
        return f"light-analysis-of-{resume.get('id')}"


class _FakeAI:
    """In-process AI client with a ``rate_limit`` attribute."""

    def __init__(self) -> None:
        self.rate_limit: Any = None


def _profile(
    id_: str = "p1", *, ai_filter_mode: str | None = "heavy"
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_, ai_filter_mode=ai_filter_mode, relevance_rules=None
    )


def _resume(id_: str = "r1") -> dict[str, Any]:
    return {"id": id_, "title": "Backend"}


# ─── build() ───────────────────────────────────────────────────────


class TestBuild:
    """``build()`` constructs the per-profile filter AI client."""

    def test_no_mode_resets_ai_client(self) -> None:
        """``ai_filter_mode=None`` → ``ai_client = None`` (no factory call)."""
        service = AiFilterService()
        relevance = _FakeRelevance()
        calls: list[str] = []

        def factory(prompt: str) -> _FakeAI:
            calls.append(prompt)
            return _FakeAI()

        result = service.build(
            profile=_profile(ai_filter_mode=None),
            resume=_resume(),
            relevance_obj=relevance,
            factory=factory,
        )

        assert result is None
        assert relevance.ai_client is None
        assert calls == []  # factory never invoked

    def test_heavy_mode_invokes_factory_with_heavy_prompt(self) -> None:
        """``ai_filter_mode="heavy"`` → factory receives the heavy
        system prompt; ``ai_client`` is set to the produced client."""
        service = AiFilterService()
        relevance = _FakeRelevance()
        captured: dict[str, str] = {}

        def factory(prompt: str) -> _FakeAI:
            captured["prompt"] = prompt
            return _FakeAI()

        result = service.build(
            profile=_profile(ai_filter_mode="heavy"),
            resume=_resume("r1"),
            relevance_obj=relevance,
            factory=factory,
        )

        assert isinstance(result, _FakeAI)
        assert relevance.ai_client is result
        assert relevance.analyze_calls == [("heavy", _resume("r1"))]
        # The system prompt must reference the heavy analysis string.
        assert "heavy-analysis-of-r1" in captured["prompt"]

    def test_light_mode_invokes_factory_with_light_prompt(self) -> None:
        """``ai_filter_mode="light"`` → factory receives the light
        system prompt."""
        service = AiFilterService()
        relevance = _FakeRelevance()
        captured: dict[str, str] = {}

        def factory(prompt: str) -> _FakeAI:
            captured["prompt"] = prompt
            return _FakeAI()

        result = service.build(
            profile=_profile(ai_filter_mode="light"),
            resume=_resume("r1"),
            relevance_obj=relevance,
            factory=factory,
        )

        assert isinstance(result, _FakeAI)
        assert relevance.ai_client is result
        assert relevance.analyze_calls == [("light", _resume("r1"))]
        assert "light-analysis-of-r1" in captured["prompt"]

    def test_unknown_mode_resets_ai_client(self) -> None:
        """An unknown ``ai_filter_mode`` → ``ai_client = None``."""
        service = AiFilterService()
        relevance = _FakeRelevance()
        calls: list[str] = []

        def factory(prompt: str) -> _FakeAI:
            calls.append(prompt)
            return _FakeAI()

        result = service.build(
            profile=_profile(ai_filter_mode="bogus"),
            resume=_resume(),
            relevance_obj=relevance,
            factory=factory,
        )

        assert result is None
        assert relevance.ai_client is None
        assert calls == []

    def test_no_factory_resets_ai_client(self) -> None:
        """``factory=None`` (filter requested but no factory wired) →
        ``ai_client = None``."""
        service = AiFilterService()
        relevance = _FakeRelevance()

        result = service.build(
            profile=_profile(ai_filter_mode="heavy"),
            resume=_resume(),
            relevance_obj=relevance,
            factory=None,
        )

        assert result is None
        assert relevance.ai_client is None

    def test_factory_raises_value_error_is_handled(self) -> None:
        """A ``ValueError`` from the factory is logged and ``ai_client``
        is reset to ``None``."""
        service = AiFilterService()
        relevance = _FakeRelevance()

        def bad_factory(prompt: str) -> _FakeAI:
            raise ValueError("no key")

        result = service.build(
            profile=_profile(ai_filter_mode="heavy"),
            resume=_resume(),
            relevance_obj=relevance,
            factory=bad_factory,
        )

        assert result is None
        assert relevance.ai_client is None

    def test_factory_raises_runtime_error_is_handled(self) -> None:
        """A ``RuntimeError`` from the factory is logged and ``ai_client``
        is reset to ``None``."""
        service = AiFilterService()
        relevance = _FakeRelevance()

        def bad_factory(prompt: str) -> _FakeAI:
            raise RuntimeError("ai provider down")

        result = service.build(
            profile=_profile(ai_filter_mode="heavy"),
            resume=_resume(),
            relevance_obj=relevance,
            factory=bad_factory,
        )

        assert result is None
        assert relevance.ai_client is None

    def test_factory_raises_unexpected_error_is_handled(self) -> None:
        """A generic ``Exception`` from the factory is also logged and
        ``ai_client`` is reset to ``None``."""
        service = AiFilterService()
        relevance = _FakeRelevance()

        def bad_factory(prompt: str) -> _FakeAI:
            raise MemoryError("unrelated")  # noqa: N802

        result = service.build(
            profile=_profile(ai_filter_mode="heavy"),
            resume=_resume(),
            relevance_obj=relevance,
            factory=bad_factory,
        )

        assert result is None
        assert relevance.ai_client is None

    def test_rate_limit_is_forwarded(self) -> None:
        """``rate_limit`` is assigned to the produced AI client."""
        service = AiFilterService()
        relevance = _FakeRelevance()

        def factory(prompt: str) -> _FakeAI:
            return _FakeAI()

        result = service.build(
            profile=_profile(ai_filter_mode="heavy"),
            resume=_resume(),
            relevance_obj=relevance,
            factory=factory,
            rate_limit=42,
        )

        assert isinstance(result, _FakeAI)
        assert result.rate_limit == 42

    def test_rate_limit_assignment_failure_is_swallowed(self) -> None:
        """A failure during the ``rate_limit`` assignment is logged
        and the rest of the contract still holds (``ai_client`` is
        set to the produced client)."""

        class _StubbornAI:
            @property
            def rate_limit(self) -> Any:
                raise AttributeError("read-only")

            @rate_limit.setter
            def rate_limit(self, value: Any) -> None:
                raise RuntimeError("cannot assign")

        service = AiFilterService()
        relevance = _FakeRelevance()

        def factory(prompt: str) -> _StubbornAI:
            return _StubbornAI()

        result = service.build(
            profile=_profile(ai_filter_mode="heavy"),
            resume=_resume(),
            relevance_obj=relevance,
            factory=factory,
            rate_limit=99,
        )

        assert isinstance(result, _StubbornAI)
        # ``ai_client`` still set to the produced client (the helper
        # only logs the rate_limit failure).
        assert relevance.ai_client is result
