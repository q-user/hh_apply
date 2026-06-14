"""E2E: vacancy search -> relevance -> cover letter -> draft (#53 + #54).

This integration test exercises the full prepare-vacancies pipeline
that the production CLI / Telegram bot use:

  1. ``VacancySearchSlice.search()`` returns N vacancies from the
     mocked HH API.
  2. For each vacancy, ``RelevanceHandler.is_suitable_heavy()``
     scores it (against the deterministic AI client).
  3. ``CoverLetterHandler.generate_cover_letter()`` produces a
     letter (also against the deterministic AI client).
  4. ``ApplicationHandler.prepare_draft()`` persists the draft.

The end-to-end payload (``status``, ``cover_letter``,
``relevance_score``) is compared against what a real
``prepare-vacancies`` run would write.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

ACCESS_TOKEN = "test-access-token"


# ─── Helpers ──────────────────────────────────────────────────────────


def _make_search_vacancy(idx: int) -> dict:
    return {
        "id": str(1000 + idx),
        "name": f"Vacancy {1000 + idx}",
        "employer": {"id": 9000 + idx, "name": f"Acme {idx}"},
        "salary": {"from": 200000, "to": 300000, "currency": "RUR"},
        "area": {"name": "Москва"},
        "alternate_url": f"https://hh.ru/vacancy/{1000 + idx}",
        "has_test": False,
        "response_letter_required": True,
    }


def _make_full_vacancy(idx: int) -> dict:
    base = _make_search_vacancy(idx)
    base["description"] = (
        "Senior Python role. We use Django, FastAPI, PostgreSQL, "
        "and a bit of Kubernetes."
    )
    base["key_skills"] = [
        {"name": "Python"},
        {"name": "Django"},
        {"name": "PostgreSQL"},
    ]
    return base


# ─── Test cases ──────────────────────────────────────────────────────


class TestVacancySearchToPrepFlow:
    """End-to-end: search -> score -> cover letter -> draft."""

    def test_search_returns_vacancies_from_mock(
        self,
        test_db,
        mock_hh_api,
        slices,
    ) -> None:
        """``VacancySearchSlice.search()`` consumes the mocked HH API
        and returns a list of vacancies.
        """
        mock_hh_api.scripted_responses[("GET", "/vacancies")] = [
            {
                "items": [_make_search_vacancy(i) for i in range(3)],
                "pages": 1,
                "page": 0,
            }
        ]
        from job_bot.vacancy_search.models.search_profile import (
            SearchProfile,
        )

        profile = SearchProfile(
            id="p1",
            name="p1",
            keywords="Python",
        )
        results = slices.vacancy_search.search.search_vacancies(
            profile, ACCESS_TOKEN, max_pages=1
        )
        assert len(results) == 3
        assert all(r.hh_id for r in results)
        # The access token was forwarded to the mock.
        assert mock_hh_api.access_token == ACCESS_TOKEN

    def test_search_score_letter_draft_end_to_end(
        self,
        test_db,
        mock_hh_api,
        mock_ai_client,
        slices,
    ) -> None:
        """Full pipeline: search -> score -> cover letter -> draft.

        Validates the cross-slice payload invariant: the draft that
        lands in the DB matches the same fields the production
        ``prepare-vacancies`` use case would write.
        """
        from hh_applicant_tool.storage import StorageFacade
        from job_bot.vacancy_search.models.search_profile import (
            SearchProfile,
        )

        # Configure the mock to return 2 search results, each with a
        # full-vacancy detail endpoint.
        vacancy_ids = [1000, 1001]
        mock_hh_api.scripted_responses[("GET", "/vacancies")] = [
            {
                "items": [_make_search_vacancy(i) for i in range(2)],
                "pages": 1,
                "page": 0,
            }
        ]
        for vid in vacancy_ids:
            mock_hh_api.scripted_responses[("GET", f"/vacancies/{vid}")] = [
                _make_full_vacancy(vid - 1000)
            ]
        # AI client returns "suitable" deterministically.
        mock_ai_client.mode = "suitable"

        # 1) Vacancy search slice: returns Vacancy objects.
        profile = SearchProfile(
            id="p1",
            name="p1",
            keywords="Python",
        )
        vacancies = slices.vacancy_search.search.search_vacancies(
            profile, ACCESS_TOKEN, max_pages=1
        )
        assert len(vacancies) == 2

        # 2) For each vacancy, fetch the full version, score it, and
        #    generate a cover letter via the application prep slice.
        drafts = []
        for vacancy in vacancies:
            full = mock_hh_api.get(f"/vacancies/{vacancy.hh_id}").json()

            # Relevance: heavy path.
            relevance_result = (
                slices.application_prep.relevance.is_suitable_heavy(full)
            )
            # With the deterministic AI in "suitable" mode, the score
            # is 85 and the vacancy is suitable.
            assert relevance_result.suitable is True
            assert relevance_result.score == 85

            # Cover letter: AI generated
            letter = (
                slices.application_prep.cover_letters.generate_cover_letter(
                    vacancy=full,
                    placeholders={"first_name": "Ivan"},
                    resume={"id": "r1", "title": "Senior Python"},
                    force=True,
                    required_by_vacancy=True,
                )
            )
            assert letter is not None
            assert "hash=" in letter  # deterministic AI generated

            # Persist the draft through the application handler.
            draft = slices.application_prep.applications.prepare_draft(
                resume={"id": "r1", "title": "Senior Python"},
                vacancy=full,
                search_profile_id="p1",
                ai_filter_mode="heavy",
                placeholders={"first_name": "Ivan"},
                force_message=True,
            )
            assert draft is not None
            drafts.append(draft)

        # 3) Verify the drafts landed in the DB with the expected
        #    fields. The ``ApplicationDraftModel`` is the cross-slice
        #    payload: status, relevance_score, cover_letter, etc.
        facade = StorageFacade(test_db)
        for draft in drafts:
            assert draft.id is not None
            db_draft = facade.application_drafts.get(draft.id)
            assert db_draft is not None
            assert db_draft.status == "prepared"
            assert db_draft.cover_letter is not None
            assert "hash=" in db_draft.cover_letter
            assert db_draft.relevance_score == 85
            assert db_draft.cover_letter_status == "generated"
            assert db_draft.search_profile_id == "p1"

    def test_draft_payload_matches_production_prepare_one(
        self,
        test_db,
        mock_ai_client,
        slices,
    ) -> None:
        """The draft payload produced by the new slice must match
        what a real ``prepare-vacancies`` run would write.

        We assert against the ``ApplicationDraftModel`` contract:
          * status="prepared" (not "rejected"),
          * cover_letter_status="generated",
          * cover_letter is non-empty,
          * has_test=False (the test vacancy has no test),
          * search_profile_id is the profile that owned the search.
        """
        mock_ai_client.mode = "suitable"
        full_vacancy = _make_full_vacancy(0)
        draft = slices.application_prep.applications.prepare_draft(
            resume={"id": "r1", "title": "Senior Python"},
            vacancy=full_vacancy,
            search_profile_id="django-senior",
            ai_filter_mode="heavy",
            placeholders={"first_name": "Ivan"},
            force_message=True,
        )
        assert draft is not None
        # The slice writes the same shape the production use case does.
        assert draft.status == "prepared"
        assert draft.cover_letter_status == "generated"
        assert draft.cover_letter
        assert draft.has_test is False
        assert draft.search_profile_id == "django-senior"
        assert draft.relevance_score == 85
