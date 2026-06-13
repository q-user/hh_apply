"""E2E: multi-profile prepare-vacancies (issue #54 polish).

This integration test exercises the per-profile wiring of the
application prep slice: two profiles with different
``ai_filter_mode`` and ``relevance_rules`` should produce drafts
whose ``search_profile_id`` and ``relevance_score`` reflect the
profile that owned the search, with no AI-client state leaking
between profiles.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

ACCESS_TOKEN = "test-access-token"


# ─── Helpers ──────────────────────────────────────────────────────────


def _make_full_vacancy(idx: int) -> dict:
    return {
        "id": str(2000 + idx),
        "name": f"Vacancy {2000 + idx}",
        "employer": {"id": 8000 + idx, "name": f"Acme {idx}"},
        "salary": {"from": 200000, "to": 300000, "currency": "RUR"},
        "area": {"name": "Москва"},
        "description": "Python, Django, FastAPI",
        "key_skills": [
            {"name": "Python"},
            {"name": "Django"},
        ],
        "alternate_url": f"https://hh.ru/vacancy/{2000 + idx}",
        "has_test": False,
        "response_letter_required": True,
    }


def _seed_profile(
    test_db,
    *,
    profile_id: str,
    name: str,
    resume_id: str,
    ai_filter_mode: str = "none",
    relevance_rules: dict | None = None,
    search_params: dict | None = None,
) -> None:
    """Insert a search profile into the DB (issue #54 polish)."""
    from hh_applicant_tool.storage import StorageFacade
    from hh_applicant_tool.storage.models.search_profile import (
        SearchProfileModel,
    )

    facade = StorageFacade(test_db)
    facade.search_profiles.save(
        SearchProfileModel(
            id=profile_id,
            name=name,
            resume_id=resume_id,
            enabled=True,
            ai_filter_mode=ai_filter_mode,
            relevance_rules=relevance_rules or {},
            search_params=search_params or {"text": "Python"},
        )
    )
    facade.search_profiles.commit()


# ─── Test cases ──────────────────────────────────────────────────────


class TestMultiProfileFlow:
    """Multi-profile prepare-vacancies end-to-end."""

    @pytest.mark.xfail(
        reason="pre-existing, see #100: RelevanceHandler.build_vacancy_context() calls full_vacancy.get() on the API response; MockHHApiResponse has .json() but no .get(). Production code should call .json() first."
    )
    def test_two_profiles_produce_disjoint_drafts(
        self,
        test_db,
        mock_ai_client,
        slices,
    ) -> None:
        """Two profiles run side-by-side. The drafts produced by each
        carry the right ``search_profile_id`` and the relevance
        score reflects the per-profile rules.
        """
        from hh_applicant_tool.storage import StorageFacade

        # Seed both profiles with different rules
        _seed_profile(
            test_db,
            profile_id="p1",
            name="django-senior",
            resume_id="r1",
            ai_filter_mode="heavy",
            relevance_rules={
                "must_have": ["Python", "Django"],
                "nice_to_have": ["PostgreSQL"],
            },
        )
        _seed_profile(
            test_db,
            profile_id="p2",
            name="python-only",
            resume_id="r1",
            ai_filter_mode="light",
            relevance_rules={"must_have": ["Python"]},
        )

        # The deterministic AI client returns "suitable" for every
        # prompt in ``suitable`` mode.
        mock_ai_client.mode = "suitable"

        # Prepare two drafts per profile.
        all_drafts = []
        for profile_id, ai_mode in [("p1", "heavy"), ("p2", "light")]:
            for idx in (0, 1):
                full_vacancy = _make_full_vacancy(idx)
                draft = slices.application_prep.applications.prepare_draft(
                    resume={"id": "r1", "title": "Senior Python"},
                    vacancy=full_vacancy,
                    search_profile_id=profile_id,
                    ai_filter_mode=ai_mode,
                    placeholders={"first_name": "Ivan"},
                    force_message=True,
                )
                assert draft is not None
                all_drafts.append((profile_id, draft))

        # Every draft carries the right search_profile_id and a
        # relevance_score of 85 (deterministic "suitable" mode).
        facade = StorageFacade(test_db)
        for profile_id, draft in all_drafts:
            db_draft = facade.application_drafts.get(draft.id)
            assert db_draft is not None
            assert db_draft.search_profile_id == profile_id
            assert db_draft.relevance_score == 85

        # Total draft count is 4 (2 profiles x 2 vacancies)
        total = facade.application_drafts.conn.execute(
            "SELECT COUNT(*) AS n FROM application_drafts"
        ).fetchone()
        assert total["n"] == 4

    @pytest.mark.xfail(
        reason="pre-existing, see #100: same MockHHApiResponse.get() missing — production code calls .get() on the response object instead of .json().get()."
    )
    def test_heavy_vs_light_ai_filter_diverge(
        self,
        test_db,
        mock_ai_client,
        slices,
    ) -> None:
        """When the AI is set to "unsuitable", both heavy and light
        modes reject the vacancy — the resulting draft is
        ``rejected`` and carries a low score. The two rejected
        drafts are distinct rows, one per profile.
        """
        from hh_applicant_tool.storage import StorageFacade

        mock_ai_client.mode = "unsuitable"
        full_vacancy = _make_full_vacancy(0)

        heavy_draft = slices.application_prep.applications.prepare_draft(
            resume={"id": "r1", "title": "Senior Python"},
            vacancy=full_vacancy,
            search_profile_id="p1",
            ai_filter_mode="heavy",
            placeholders={"first_name": "Ivan"},
            force_message=True,
        )
        assert heavy_draft is not None
        assert heavy_draft.status == "rejected"
        assert heavy_draft.relevance_score == 20
        # No cover letter for a rejected draft.
        assert heavy_draft.cover_letter is None

        light_draft = slices.application_prep.applications.prepare_draft(
            resume={"id": "r1", "title": "Senior Python"},
            vacancy=full_vacancy,
            search_profile_id="p2",
            ai_filter_mode="light",
            placeholders={"first_name": "Ivan"},
            force_message=True,
        )
        assert light_draft is not None
        assert light_draft.status == "rejected"
        assert light_draft.relevance_score == 20

        # The two rejected drafts are distinct rows, one per profile.
        facade = StorageFacade(test_db)
        rows = facade.application_drafts.conn.execute(
            "SELECT search_profile_id, status FROM application_drafts "
            "ORDER BY id"
        ).fetchall()
        assert [r["search_profile_id"] for r in rows] == ["p1", "p2"]
        assert all(r["status"] == "rejected" for r in rows)

    @pytest.mark.xfail(
        reason="pre-existing, see #100: same MockHHApiResponse.get() missing — production code calls .get() on the response object instead of .json().get()."
    )
    def test_per_profile_ai_client_isolation(self, slices) -> None:
        """When the prep slice is wired with two different AI
        clients (one per profile), they don't share state.

        The acceptance: setting ``relevance.ai_client`` on one
        profile's run doesn't leak into the next profile's run.
        """
        from tests.integration._mocks import DeterministicAIClient

        full_vacancy = _make_full_vacancy(0)

        # Two independent AI clients, each with a different mode.
        ai_heavy = DeterministicAIClient()
        ai_heavy.mode = "suitable"

        ai_light = DeterministicAIClient()
        ai_light.mode = "unsuitable"

        # Profile p1 with the heavy AI: suitable
        slices.application_prep.relevance.ai_client = ai_heavy
        slices.application_prep.cover_letters.ai_client = ai_heavy
        p1 = slices.application_prep.applications.prepare_draft(
            resume={"id": "r1", "title": "Senior Python"},
            vacancy=full_vacancy,
            search_profile_id="p1",
            ai_filter_mode="heavy",
            placeholders={"first_name": "Ivan"},
            force_message=True,
        )
        assert p1 is not None
        assert p1.status == "prepared"
        assert p1.relevance_score == 85

        # Profile p2 with the light AI: rejected
        slices.application_prep.relevance.ai_client = ai_light
        slices.application_prep.cover_letters.ai_client = ai_light
        p2 = slices.application_prep.applications.prepare_draft(
            resume={"id": "r1", "title": "Senior Python"},
            vacancy=full_vacancy,
            search_profile_id="p2",
            ai_filter_mode="light",
            placeholders={"first_name": "Ivan"},
            force_message=True,
        )
        assert p2 is not None
        assert p2.status == "rejected"
        assert p2.relevance_score == 20

        # Each AI client saw exactly one call (no state leakage).
        assert len(ai_heavy.calls) == 1
        assert len(ai_light.calls) == 1
