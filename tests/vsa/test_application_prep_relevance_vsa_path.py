"""VSA bridge tests for relevance (issue #135).

The VSA ``RelevanceHandler`` is the single source of truth for AI
relevance filtering. ``ApplicationPrepSlice`` must NOT fall back to the
legacy ``RelevanceService`` shim
anymore. These tests pin that contract and mirror the legacy
``tests/test_services_relevance.py`` / ``tests/test_relevance_failure_modes.py``
behaviour at the slice boundary (issue #135 acceptance criteria).

Note on ``AIError``:
    The VSA ``RelevanceHandler`` uses its own module-local ``AIError``
    class for the failure-mode machinery (strict / raise). In real
    production the AI client (e.g. OpenAI's ``OpenAIError``)
    raises a different ``AIError`` subclass from
    the AI base module. Bridging that gap is a separate
    issue — the tests below exercise the VSA handler's local contract
    with its OWN ``AIError`` class, exactly as the handler documents.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from job_bot.application_prep import (
    ApplicationPrepSlice,
)
from job_bot.application_prep.handlers.relevance_handler import (
    MAX_RETRIES,
    RelevanceHandler,
    parse_ai_json_response,
)
from job_bot.application_prep.handlers.relevance_handler import (
    AIError as VsaAIError,
)
from job_bot.application_prep.models.relevance import (
    RelevanceResult as VsaRelevanceResult,
)
from job_bot.application_prep.utils import build_filter_ai_client
from job_bot.shared.storage.database import Database, create_database

# ─── Helpers / fixtures ──────────────────────────────────────────


class _FakeAIClient:
    """Deterministic fake AI client for relevance tests.

    Records every ``complete`` call and returns canned responses in
    order (or a single string if no list is provided). Tracks
    ``rate_limit`` assignments to verify the per-profile contract.
    """

    def __init__(
        self,
        responses: Any = '{"suitable": true}',
        *,
        side_effect: BaseException | None = None,
    ) -> None:
        self._responses = responses
        self._side_effect = side_effect
        self.calls: list[str] = []
        self.rate_limit: Any = None

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self._side_effect is not None:
            raise self._side_effect
        if isinstance(self._responses, list):
            if not self._responses:
                raise AssertionError("no more canned responses left")
            return self._responses.pop(0)
        return self._responses


class _FakeApi:
    """Stand-in for ``HHApiClient`` covering only the endpoints the
    relevance handler hits (``/vacancies/{id}``, ``/resumes/{id}``)."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.errors: dict[str, BaseException] = {}
        self.calls: list[str] = []

    def get(self, path: str) -> Any:
        self.calls.append(path)
        if path in self.errors:
            raise self.errors[path]
        return self.responses.get(path, {})


def _make_handler(
    database: Database,
    *,
    api: _FakeApi | None = None,
    ai: _FakeAIClient | None = None,
    relevance_rules: dict[str, Any] | None = None,
    ai_failure_mode: str = "permissive",
) -> RelevanceHandler:
    return RelevanceHandler(
        database,
        api_client=api,
        ai_client=ai,
        relevance_rules=relevance_rules,
        ai_failure_mode=ai_failure_mode,
    )


@pytest.fixture
def database(temp_db_path: Path) -> Database:
    return create_database(temp_db_path)


# ─── 1. Slice contract: no longer wires the legacy RelevanceService ──


class TestSliceNoLongerImportsLegacyRelevance:
    """The slice must not import or instantiate the legacy
    ``RelevanceService`` shim at runtime (issue #135).
    """

    def test_slice_does_not_import_hh_applicant_tool_services_relevance(
        self, database: Database
    ) -> None:
        """Constructing the slice does not import the legacy relevance
        shim at all. Removing the import is the whole point of issue #135.
        """
        # Drop the shim from ``sys.modules`` and verify the slice build
        # does not pull it back in.
        sys.modules.pop("hh_applicant_tool.services.relevance", None)
        ApplicationPrepSlice(database=database)
        assert "hh_applicant_tool.services.relevance" not in sys.modules, (
            "ApplicationPrepSlice must not import the legacy relevance "
            "shim (issue #135); the VSA RelevanceHandler is the single "
            "source of truth."
        )

    def test_slice_source_does_not_reference_legacy_relevance_shim(
        self, database: Database
    ) -> None:
        """The slice module's source must not reference the legacy
        ``RelevanceService`` shim (issue #135).

        This is a static check: any reference — import, attribute access,
        or string mention — indicates the legacy shim is still wired in.
        The contract is: VSA ``RelevanceHandler`` is the only relevance
        implementation used by the slice.
        """
        import inspect

        from job_bot.application_prep import slice as slice_mod

        source = inspect.getsource(slice_mod)
        # The VSA handler is at
        # ``job_bot.application_prep.handlers.relevance_handler``.
        # Block any reference to the legacy shim import path.
        assert "hh_applicant_tool.services.relevance" not in source, (
            "ApplicationPrepSlice still references the legacy "
            "hh_applicant_tool.services.relevance shim (issue #135). "
            "Remove the legacy fallback; the VSA RelevanceHandler is the "
            "single source of truth."
        )
        # Also check there's no RelevanceService symbol imported.
        assert "RelevanceService" not in source, (
            "ApplicationPrepSlice still imports the legacy "
            "RelevanceService class (issue #135). Use "
            "job_bot.application_prep.handlers.RelevanceHandler instead."
        )

    def test_slice_does_not_expose_pipeline_build_relevance_service(
        self, database: Database
    ) -> None:
        """The legacy static method ``_pipeline_build_relevance_service``
        must be gone (issue #135). It's the entry point that instantiates
        the legacy shim.
        """
        slice_ = ApplicationPrepSlice(database=database)
        assert not hasattr(slice_, "_pipeline_build_relevance_service"), (
            "ApplicationPrepSlice still exposes "
            "_pipeline_build_relevance_service (issue #135). The legacy "
            "fallback that built RelevanceService instances has been "
            "removed; use the VSA RelevanceHandler via slice.relevance."
        )

    def test_slice_relevance_property_returns_vsa_handler(
        self, database: Database
    ) -> None:
        """``slice.relevance`` is a :class:`RelevanceHandler` (VSA),
        not a legacy ``RelevanceService``.
        """
        slice_ = ApplicationPrepSlice(database=database)
        assert isinstance(slice_.relevance, RelevanceHandler)
        # And the storage port reuses the same instance.
        assert slice_.relevance_storage is slice_.relevance

    def test_slice_relevance_handler_is_the_single_shared_instance(
        self, database: Database
    ) -> None:
        """``ApplicationHandler`` and the slice's relevance port share
        the SAME ``RelevanceHandler`` instance — that's how the per-profile
        AI client setter is supposed to propagate.
        """
        slice_ = ApplicationPrepSlice(database=database)
        assert (
            slice_._application_handler.relevance is slice_._relevance_handler
        )
        assert slice_._relevance_handler is slice_.relevance


# ─── 2. VSA RelevanceHandler behaviour (mirrors legacy test_services_relevance) ──


class TestVsaRelevanceHandlerHeavyLight:
    """Mirror of the legacy ``RelevanceService`` happy-path tests."""

    def test_no_ai_client_returns_suitable_true(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        handler = _make_handler(database, api=api, ai=None)

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        assert r.suitable is True
        assert r.relevance_score is None
        assert r.reason is None

    def test_heavy_calls_api_and_ai(self, database: Database) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        ai = _FakeAIClient(
            '{"suitable": true, "relevance_score": 80, "reason": "ok"}'
        )
        handler = _make_handler(database, api=api, ai=ai)

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        assert r.suitable is True
        assert r.relevance_score == 80
        assert r.reason == "ok"
        assert "/vacancies/1" in api.calls
        assert ai.calls and len(ai.calls) == 1

    def test_light_does_not_include_full_description_in_prompt(
        self, database: Database
    ) -> None:
        """Light path must NOT include the full vacancy description,
        only the vacancy name + key_skills. The ``/vacancies/{id}`` call
        IS made to fetch key_skills (this is by design — see
        :meth:`RelevanceHandler.build_vacancy_context`)."""
        api = _FakeApi()
        api.responses["/vacancies/1"] = {
            "description": "<p>HUGE FULL DESCRIPTION</p>",
            "key_skills": [{"name": "Go"}],
        }
        ai = _FakeAIClient('{"suitable": true}')
        handler = _make_handler(database, api=api, ai=ai)

        handler.is_suitable_light({"id": 1, "name": "Backend"})

        prompt = ai.calls[0]
        # key_skills IS in the prompt (it's the light path's signal).
        assert "Go" in prompt
        # The full description is NOT.
        assert "HUGE FULL DESCRIPTION" not in prompt
        assert "Описание" not in prompt


# ─── 3. Cache, retries, JSON parsing (mirror of legacy tests) ──────


class TestVsaRelevanceHandlerCacheAndRetries:
    """Resume-analysis cache, JSON retry, and ``max_retries`` fall-through."""

    def test_analyze_resume_heavy_caches(self, database: Database) -> None:
        api = _FakeApi()
        api.responses["/resumes/r1"] = {
            "title": "X",
            "skill_set": ["Python"],
            "experience": [],
        }
        handler = _make_handler(database, api=api, ai=None)

        out1 = handler.analyze_resume_heavy({"id": "r1"})
        out2 = handler.analyze_resume_heavy({"id": "r1"})

        assert out1 == out2
        assert "Python" in out1
        assert api.calls.count("/resumes/r1") == 1

    def test_analyze_resume_heavy_handles_request_exception(
        self, database: Database
    ) -> None:
        """VSA catches ``(requests.RequestException, ValueError)`` and
        returns empty string. The legacy service caught ``Exception``;
        the VSA is intentionally narrower."""
        api = _FakeApi()
        api.errors["/resumes/r1"] = requests.RequestException("boom")
        handler = _make_handler(database, api=api, ai=None)

        out = handler.analyze_resume_heavy({"id": "r1"})

        assert out == ""

    def test_analyze_resume_heavy_handles_value_error(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.errors["/resumes/r1"] = ValueError("bad json")
        handler = _make_handler(database, api=api, ai=None)

        out = handler.analyze_resume_heavy({"id": "r1"})

        assert out == ""

    def test_analyze_resume_heavy_no_id_returns_empty(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        handler = _make_handler(database, api=api, ai=None)

        out = handler.analyze_resume_heavy({})

        assert out == ""
        assert api.calls == []

    def test_analyze_resume_light_caches(self, database: Database) -> None:
        api = _FakeApi()
        api.responses["/resumes/r1"] = {
            "title": "Backend",
            "skill_set": ["Go"],
        }
        handler = _make_handler(database, api=api, ai=None)

        out1 = handler.analyze_resume_light({"id": "r1"})
        out2 = handler.analyze_resume_light({"id": "r1"})

        assert out1 == out2
        assert "Backend" in out1
        assert "Go" in out1
        assert api.calls.count("/resumes/r1") == 1

    def test_analyze_resume_light_handles_error(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.errors["/resumes/r1"] = requests.RequestException("boom")
        handler = _make_handler(database, api=api, ai=None)

        out = handler.analyze_resume_light({"id": "r1"})

        assert out == ""

    def test_retries_invalid_json(self, database: Database) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "desc"}
        ai = _FakeAIClient(["not json", '{"suitable": true}'])
        handler = _make_handler(database, api=api, ai=ai)

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        assert r.suitable is True
        assert len(ai.calls) == 2

    def test_gives_up_after_max_retries(self, database: Database) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "desc"}
        ai = _FakeAIClient("garbage")  # never parses
        handler = _make_handler(database, api=api, ai=ai)

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        # permissive mode: after max_retries → suitable=True
        assert r.suitable is True
        assert len(ai.calls) == MAX_RETRIES

    def test_handles_missing_vacancy_id_heavy(self, database: Database) -> None:
        """No vacancy.id → no /vacancies/None call."""
        api = _FakeApi()
        ai = _FakeAIClient('{"suitable": true}')
        handler = _make_handler(database, api=api, ai=ai)

        r = handler.is_suitable_heavy({"name": "X"})

        assert r.suitable is True
        for call in api.calls:
            assert "/vacancies/None" not in str(call)


# ─── 4. Failure modes (mirror of test_relevance_failure_modes) ─────


class TestVsaRelevanceHandlerFailureModes:
    """permissive / strict / raise mirrors legacy behaviour.

    The VSA handler catches its OWN local ``AIError`` class. The legacy
    service caught the AI base module's ``AIError``. Tests below
    exercise the VSA contract with the VSA's local ``AIError`` so they
    hit the same code path that production code WOULD hit IF the AI
    client factory returned an AI client raising the VSA error.
    """

    def test_invalid_ai_failure_mode_raises(self, database: Database) -> None:
        with pytest.raises(ValueError, match="ai_failure_mode"):
            _make_handler(database, ai_failure_mode="wrong_mode")

    def test_default_ai_failure_mode_is_permissive(
        self, database: Database
    ) -> None:
        handler = _make_handler(database)
        assert handler._ai_failure_mode == "permissive"

    def test_permissive_ai_error_returns_suitable_true(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        ai = _FakeAIClient(side_effect=VsaAIError("rate limit"))
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="permissive"
        )

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        assert r.suitable is True
        assert r.raw_response is not None
        assert "rate limit" in r.raw_response

    def test_permissive_max_retries_returns_suitable_true(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        ai = _FakeAIClient("not a json")
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="permissive"
        )

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        assert r.suitable is True
        assert len(ai.calls) == MAX_RETRIES

    def test_strict_ai_error_returns_suitable_false(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        ai = _FakeAIClient(side_effect=VsaAIError("api down"))
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="strict"
        )

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        assert r.suitable is False

    def test_strict_max_retries_returns_suitable_false(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        ai = _FakeAIClient("not a json")
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="strict"
        )

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        assert r.suitable is False
        assert len(ai.calls) == MAX_RETRIES

    def test_raise_ai_error_propagates(self, database: Database) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        ai = _FakeAIClient(side_effect=VsaAIError("fatal"))
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="raise"
        )

        with pytest.raises(VsaAIError):
            handler.is_suitable_heavy({"id": 1, "name": "X"})

    def test_raise_max_retries_raises(self, database: Database) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        ai = _FakeAIClient("not a json")
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="raise"
        )

        with pytest.raises(VsaAIError):
            handler.is_suitable_heavy({"id": 1, "name": "X"})
        assert len(ai.calls) == MAX_RETRIES

    def test_strict_ai_error_on_light(self, database: Database) -> None:
        """Strict mode works in the light path too."""
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"key_skills": [{"name": "Go"}]}
        ai = _FakeAIClient(side_effect=VsaAIError("api down"))
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="strict"
        )

        r = handler.is_suitable_light({"id": 1, "name": "X"})

        assert r.suitable is False

    def test_raise_ai_error_on_light(self, database: Database) -> None:
        """Raise mode works in the light path too."""
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"key_skills": [{"name": "Go"}]}
        ai = _FakeAIClient(side_effect=VsaAIError("fatal"))
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="raise"
        )

        with pytest.raises(VsaAIError):
            handler.is_suitable_light({"id": 1, "name": "X"})

    def test_strict_records_failure_reason(self, database: Database) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        ai = _FakeAIClient(side_effect=VsaAIError("rate limit exceeded"))
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="strict"
        )

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        assert r.suitable is False
        assert r.raw_response is not None
        assert "rate limit" in r.raw_response

    def test_permissive_records_failure_reason(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {"description": "<p>X</p>"}
        ai = _FakeAIClient(side_effect=VsaAIError("api timeout"))
        handler = _make_handler(
            database, api=api, ai=ai, ai_failure_mode="permissive"
        )

        r = handler.is_suitable_heavy({"id": 1, "name": "X"})

        assert r.suitable is True
        assert r.raw_response is not None
        assert "api timeout" in r.raw_response


# ─── 5. Vacancy context helpers ──────────────────────────────────


class TestVsaVacancyContextHelpers:
    """``get_vacancy_key_skills`` and ``build_vacancy_context``."""

    def test_get_vacancy_key_skills_joins_names(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.responses["/vacancies/42"] = {
            "key_skills": [
                {"name": "Python"},
                {"name": "Django"},
                {"name": ""},
            ]
        }
        handler = _make_handler(database, api=api, ai=None)

        out = handler.get_vacancy_key_skills(42)

        assert "Python" in out
        assert "Django" in out

    def test_get_vacancy_key_skills_handles_error(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.errors["/vacancies/42"] = RuntimeError("boom")
        handler = _make_handler(database, api=api, ai=None)

        out = handler.get_vacancy_key_skills(42)

        assert out == ""

    def test_get_vacancy_key_skills_no_api_returns_empty(
        self, database: Database
    ) -> None:
        handler = _make_handler(database, api=None, ai=None)

        out = handler.get_vacancy_key_skills(42)

        assert out == ""

    def test_build_vacancy_context_includes_name(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        handler = _make_handler(database, api=api, ai=None)

        out = handler.build_vacancy_context(
            {"id": 1, "name": "Python Developer"}
        )

        assert "Python Developer" in out

    def test_build_vacancy_context_includes_description_heavy(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        handler = _make_handler(database, api=api, ai=None)

        out = handler.build_vacancy_context(
            {"id": 1, "name": "X"},
            full_vacancy={"description": "<p>Some description</p>"},
            include_full=True,
        )

        assert "Some description" in out

    def test_build_vacancy_context_light_uses_key_skills(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.responses["/vacancies/1"] = {
            "key_skills": [{"name": "Go"}, {"name": "Kafka"}]
        }
        handler = _make_handler(database, api=api, ai=None)

        out = handler.build_vacancy_context(
            {"id": 1, "name": "X"},
            full_vacancy=None,
            include_full=False,
        )

        assert "Go" in out
        assert "Kafka" in out


# ─── 6. JSON parsing edge cases (mirror of legacy test_parse_*) ────


class TestVsaParseAiJsonResponse:
    """``parse_ai_json_response`` covers the same forms as the legacy
    parser (bool-only, plain JSON, fenced JSON, fallback regex).
    """

    def test_yes_no_responses(self) -> None:
        for token, expected in (
            ("да", True),
            ("yes", True),
            ("true", True),
            ("нет", False),
            ("no", False),
            ("false", False),
        ):
            r = parse_ai_json_response(token)
            assert r is not None
            assert r.suitable is expected
            assert r.raw_response == token

    def test_yes_no_case_insensitive(self) -> None:
        assert parse_ai_json_response("ДА").suitable is True
        assert parse_ai_json_response("Yes").suitable is True
        assert parse_ai_json_response("False").suitable is False

    def test_plain_json(self) -> None:
        r = parse_ai_json_response(
            '{"suitable": true, "relevance_score": 85, "reason": "match"}'
        )
        assert r is not None
        assert r.suitable is True
        assert r.relevance_score == 85
        assert r.reason == "match"

    def test_plain_json_false(self) -> None:
        r = parse_ai_json_response(
            '{"suitable": false, "reason": "wrong stack"}'
        )
        assert r is not None
        assert r.suitable is False
        assert r.reason == "wrong stack"
        assert r.relevance_score is None

    def test_fenced_json(self) -> None:
        r = parse_ai_json_response('```json\n{"suitable":false}\n```')
        assert r is not None
        assert r.suitable is False

    def test_fenced_json_with_text(self) -> None:
        r = parse_ai_json_response(
            'Вот ответ:\n```json\n{"suitable": true, "relevance_score": 90}\n```\nГотово.'
        )
        assert r is not None
        assert r.suitable is True
        assert r.relevance_score == 90

    def test_fallback_regex(self) -> None:
        text = (
            "Some preface text "
            '{"suitable": true, "relevance_score": 75, "reason": "ok"}'
            " trailing text"
        )
        r = parse_ai_json_response(text)
        assert r is not None
        assert r.suitable is True
        assert r.relevance_score == 75
        assert r.reason == "ok"

    def test_returns_none_on_garbage(self) -> None:
        assert parse_ai_json_response("not a json") is None
        assert parse_ai_json_response("42") is None
        assert parse_ai_json_response("hello world") is None

    def test_returns_none_on_empty(self) -> None:
        assert parse_ai_json_response("") is None
        assert parse_ai_json_response("   ") is None
        assert parse_ai_json_response(None) is None

    def test_invalid_score_keeps_none(self) -> None:
        r = parse_ai_json_response(
            '{"suitable": true, "relevance_score": "abc"}'
        )
        assert r is not None
        assert r.suitable is True
        assert r.relevance_score is None


# ─── 7. Per-profile filter AI client injection (the contract the
#      legacy fallback used to satisfy via ``_pipeline_build_relevance_service``) ──


class TestPerProfileFilterAiClientInjection:
    """``build_filter_ai_client`` must keep working on the VSA
    ``RelevanceHandler`` after the legacy fallback is gone (issue #135).
    """

    def test_heavy_mode_injects_ai_client(self, database: Database) -> None:
        api = _FakeApi()
        api.responses["/resumes/r1"] = {
            "title": "X",
            "skill_set": ["Python"],
            "experience": [],
        }
        handler = _make_handler(database, api=api, ai=None)
        ai = _FakeAIClient('{"suitable": true}')

        profile = MagicMock(ai_filter_mode="heavy", relevance_rules=None)
        result = build_filter_ai_client(
            profile=profile,
            resume={"id": "r1"},
            relevance_obj=handler,
            factory=lambda _prompt: ai,
            rate_limit=40,
        )

        assert result is ai
        assert handler.ai_client is ai
        assert ai.rate_limit == 40

    def test_no_mode_clears_ai_client(self, database: Database) -> None:
        api = _FakeApi()
        handler = _make_handler(database, api=api, ai=None)
        handler.ai_client = MagicMock()

        profile = MagicMock(ai_filter_mode=None)
        result = build_filter_ai_client(
            profile=profile,
            resume={"id": "r1"},
            relevance_obj=handler,
            factory=MagicMock(),
        )

        assert result is None
        assert handler.ai_client is None

    def test_unknown_mode_clears_ai_client(self, database: Database) -> None:
        api = _FakeApi()
        handler = _make_handler(database, api=api, ai=None)
        handler.ai_client = MagicMock()

        profile = MagicMock(ai_filter_mode="bogus", relevance_rules=None)
        result = build_filter_ai_client(
            profile=profile,
            resume={"id": "r1"},
            relevance_obj=handler,
            factory=MagicMock(),
        )

        assert result is None
        assert handler.ai_client is None

    def test_no_factory_clears_ai_client(self, database: Database) -> None:
        api = _FakeApi()
        handler = _make_handler(database, api=api, ai=None)
        handler.ai_client = MagicMock()

        profile = MagicMock(ai_filter_mode="heavy", relevance_rules=None)
        result = build_filter_ai_client(
            profile=profile,
            resume={"id": "r1"},
            relevance_obj=handler,
            factory=None,
        )

        assert result is None
        assert handler.ai_client is None

    def test_light_mode_uses_analyze_resume_light(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        api.responses["/resumes/r1"] = {"title": "Backend", "skill_set": ["Go"]}
        handler = _make_handler(database, api=api, ai=None)
        ai = _FakeAIClient('{"suitable": true}')

        profile = MagicMock(ai_filter_mode="light", relevance_rules=None)
        result = build_filter_ai_client(
            profile=profile,
            resume={"id": "r1"},
            relevance_obj=handler,
            factory=lambda _prompt: ai,
        )

        assert result is ai
        # The light analysis was used (cache populated).
        cached = handler.analyze_resume_light({"id": "r1"})
        assert "Go" in cached
        # No heavy fetch in light path — only one /resumes/r1 call.
        assert api.calls.count("/resumes/r1") == 1

    def test_factory_exception_clears_ai_client(
        self, database: Database
    ) -> None:
        api = _FakeApi()
        handler = _make_handler(database, api=api, ai=None)
        handler.ai_client = MagicMock()

        def bad_factory(_prompt: str):
            raise RuntimeError("AI unavailable")

        profile = MagicMock(ai_filter_mode="heavy", relevance_rules=None)
        result = build_filter_ai_client(
            profile=profile,
            resume={"id": "r1"},
            relevance_obj=handler,
            factory=bad_factory,
        )

        assert result is None
        assert handler.ai_client is None


# ─── 8. Save/get analysis (storage port) ─────────────────────────


class TestVsaSaveAndGetAnalysis:
    """The ``RelevanceStoragePort`` half of the handler (issue #135)."""

    def test_save_and_get_roundtrip(self, database: Database) -> None:
        handler = _make_handler(database)
        result = VsaRelevanceResult(
            suitable=True,
            relevance_score=90,
            reason="match",
        )

        handler.save_analysis("draft-1", result)
        loaded = handler.get_analysis("draft-1")

        assert loaded is not None
        assert loaded.suitable is True
        assert loaded.relevance_score == 90
        assert loaded.reason == "match"

    def test_get_missing_returns_none(self, database: Database) -> None:
        handler = _make_handler(database)
        assert handler.get_analysis("does-not-exist") is None
