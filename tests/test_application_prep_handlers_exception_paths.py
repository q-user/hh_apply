"""Regression tests for the new BLE001 exception paths in
``src/job_bot/application_prep/handlers/`` (PR #111 review feedback).

PR #111 replaced bare ``except Exception:`` with specific exception types
in three handler modules:

* ``application_handler.py:199``  — ``except sqlite3.Error``
  (``_cover_letter_repo.save`` is a DB call).
* ``cover_letter_handler.py:167`` — ``except sqlite3.Error``
  (``vacancy_port.get_vacancy_by_hh_id`` is a DB-backed lookup).
* ``cover_letter_handler.py:177`` — ``except (requests.RequestException,
  ValueError)`` (HH API call via ``api_client.get``).
* ``relevance_handler.py:89``    — ``except (requests.RequestException,
  ValueError)`` (HH API call in ``analyze_resume_heavy``).
* ``relevance_handler.py:145``   — ``except (requests.RequestException,
  ValueError)`` (HH API call in ``analyze_resume_light``).
* ``relevance_handler.py:221``   — ``except (requests.RequestException,
  ValueError)`` (HH API call in ``is_suitable_heavy``).

These tests pin down the contract that each specific exception is actually
caught and the handler returns a graceful fallback (no crash, no re-raise),
while non-matching exceptions (e.g. ``OSError``) are NOT silently swallowed
— preventing the silent-failure footgun the BLE001 lint rule was guarding
against in the first place.

Refs #82, PR #111 review.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
import requests

from job_bot.application_prep.handlers.application_handler import (
    ApplicationHandler,
)
from job_bot.application_prep.handlers.cover_letter_handler import (
    CoverLetterHandler,
)
from job_bot.application_prep.handlers.relevance_handler import (
    RelevanceHandler,
)
from job_bot.shared.storage.database import Database

# ─── helpers ───────────────────────────────────────────────────────


def _make_temp_db_path() -> str:
    """Create a temporary file path suitable for ``Database(path)``.

    Mirrors the helper in ``test_vsa_application_prep_wiring.py``.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def temp_db_path() -> Iterator[str]:
    """Yield a temporary DB file path, cleaning it up afterwards."""
    path = _make_temp_db_path()
    try:
        yield path
    finally:
        _safe_unlink(path)


def _vacancy_dict() -> dict:
    """Return a minimal vacancy dict accepted by the handlers."""
    return {
        "id": 123,
        "name": "Senior Python",
        "employer": {"id": 42, "name": "Acme"},
        "has_test": False,
        "response_letter_required": True,
    }


def _resume_dict() -> dict:
    """Return a minimal resume dict accepted by the handlers."""
    return {"id": "r1", "title": "Senior Python Developer"}


# ─── ApplicationHandler ────────────────────────────────────────────


class TestApplicationHandlerCoverLetterRepoErrors:
    """``ApplicationHandler.prepare_draft`` must NOT crash when the
    cover-letter persistence step fails (PR #111 narrowed the bare
    ``except Exception`` to ``except sqlite3.Error``).
    """

    def test_prepare_draft_continues_when_cover_letter_save_raises_sqlite(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's new ``except sqlite3.Error`` at
        # application_handler.py:199 — the draft flow must complete
        # and the draft must still be saved (without the cover letter
        # row, which is best-effort persistence).
        db = Database(temp_db_path)
        handler = ApplicationHandler(database=db)

        # Make the cover-letter generation step return a non-empty
        # letter so we hit the persistence code path.
        cover_letter_mock = MagicMock()
        cover_letter_mock.generate_cover_letter.return_value = (
            "Test cover letter body"
        )
        handler.cover_letter = cover_letter_mock

        # Make the cover-letter repo save raise sqlite3.Error to
        # exercise the new exception clause.
        handler._cover_letter_repo = MagicMock()
        handler._cover_letter_repo.save.side_effect = sqlite3.Error("db down")

        result = handler.prepare_draft(
            resume=_resume_dict(),
            vacancy=_vacancy_dict(),
        )

        # Draft was saved (no exception propagated) and carries the
        # generated cover letter / status.
        assert result is not None
        assert result.cover_letter == "Test cover letter body"
        assert result.cover_letter_status == "generated"
        # The repo's save was called exactly once (and raised).
        handler._cover_letter_repo.save.assert_called_once()

    def test_prepare_draft_propagates_non_sqlite_error_from_cover_letter_repo(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's narrow ``except sqlite3.Error``:
        # non-DB exceptions (e.g. ``OSError``) MUST NOT be silently
        # swallowed — the BLE001 fix is only allowed to absorb DB
        # failures. If this test ever passes-without-raising, the
        # exception clause has been widened back to a bare
        # ``except Exception`` and should fail review.
        db = Database(temp_db_path)
        handler = ApplicationHandler(database=db)
        cover_letter_mock = MagicMock()
        cover_letter_mock.generate_cover_letter.return_value = (
            "Test cover letter body"
        )
        handler.cover_letter = cover_letter_mock
        handler._cover_letter_repo = MagicMock()
        handler._cover_letter_repo.save.side_effect = OSError("disk full")

        with pytest.raises(OSError, match="disk full"):
            handler.prepare_draft(
                resume=_resume_dict(),
                vacancy=_vacancy_dict(),
            )


# ─── CoverLetterHandler ────────────────────────────────────────────


class TestCoverLetterHandlerFetchFullVacancy:
    """``CoverLetterHandler._fetch_full_vacancy`` must fall through to
    the ``api_client`` fallback when the ``vacancy_port`` DB lookup
    fails, and return ``None`` when the ``api_client`` call fails
    (PR #111 added the two specific ``except`` clauses here).
    """

    def test_vacancy_port_sqlite_error_falls_through_to_api_client(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's ``except sqlite3.Error`` at
        # cover_letter_handler.py:167 — the DB-backed
        # ``vacancy_port`` lookup failure must not abort cover letter
        # generation; we should fall through to ``api_client.get``.
        db = Database(temp_db_path)
        vacancy_port = MagicMock()
        vacancy_port.get_vacancy_by_hh_id.side_effect = sqlite3.Error("db down")
        api_client = MagicMock()
        api_client.get.return_value = {
            "description": "<p>Job description</p>",
            "key_skills": [{"name": "Python"}],
        }

        handler = CoverLetterHandler(
            database=db,
            api_client=api_client,
            vacancy_port=vacancy_port,
        )

        result = handler._fetch_full_vacancy({"id": 1, "name": "X"})

        # Both layers were exercised; api_client fallback returned its
        # payload (raw_data, not a wrapped Vacancy).
        vacancy_port.get_vacancy_by_hh_id.assert_called_once_with("1")
        api_client.get.assert_called_once_with("/vacancies/1")
        assert result == {
            "description": "<p>Job description</p>",
            "key_skills": [{"name": "Python"}],
        }

    def test_api_client_request_exception_returns_none(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's
        # ``except (requests.RequestException, ValueError)`` at
        # cover_letter_handler.py:177 — network/HTTP errors must
        # return ``None`` (so the AI prompt is built with empty
        # description), not crash.
        db = Database(temp_db_path)
        api_client = MagicMock()
        api_client.get.side_effect = requests.RequestException("network down")

        handler = CoverLetterHandler(
            database=db,
            api_client=api_client,
        )

        result = handler._fetch_full_vacancy({"id": 1, "name": "X"})

        api_client.get.assert_called_once_with("/vacancies/1")
        assert result is None

    def test_api_client_value_error_returns_none(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's
        # ``except (requests.RequestException, ValueError)`` at
        # cover_letter_handler.py:177 — ``ValueError`` (typically
        # from ``raise_for_status`` or ``response.json()``) must also
        # return ``None``.
        db = Database(temp_db_path)
        api_client = MagicMock()
        api_client.get.side_effect = ValueError("bad response")

        handler = CoverLetterHandler(
            database=db,
            api_client=api_client,
        )

        result = handler._fetch_full_vacancy({"id": 1, "name": "X"})

        api_client.get.assert_called_once_with("/vacancies/1")
        assert result is None


# ─── RelevanceHandler ──────────────────────────────────────────────


class TestRelevanceHandlerResumeAnalysisErrors:
    """``RelevanceHandler.analyze_resume_heavy`` and
    ``analyze_resume_light`` must return an empty string when the
    ``api_client.get`` call fails (PR #111 added
    ``except (requests.RequestException, ValueError)`` at lines 89
    and 145).
    """

    def test_analyze_resume_heavy_returns_empty_on_request_exception(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's new clause at
        # relevance_handler.py:89.
        db = Database(temp_db_path)
        api_client = MagicMock()
        api_client.get.side_effect = requests.RequestException("network down")
        handler = RelevanceHandler(database=db, api_client=api_client)

        result = handler.analyze_resume_heavy({"id": "r1", "title": "X"})

        api_client.get.assert_called_once_with("/resumes/r1")
        assert result == ""

    def test_analyze_resume_heavy_returns_empty_on_value_error(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's new ``ValueError`` arm at
        # relevance_handler.py:89.
        db = Database(temp_db_path)
        api_client = MagicMock()
        api_client.get.side_effect = ValueError("bad response")
        handler = RelevanceHandler(database=db, api_client=api_client)

        result = handler.analyze_resume_heavy({"id": "r1", "title": "X"})

        api_client.get.assert_called_once_with("/resumes/r1")
        assert result == ""

    def test_analyze_resume_light_returns_empty_on_request_exception(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's new clause at
        # relevance_handler.py:145.
        db = Database(temp_db_path)
        api_client = MagicMock()
        api_client.get.side_effect = requests.RequestException("network down")
        handler = RelevanceHandler(database=db, api_client=api_client)

        result = handler.analyze_resume_light({"id": "r1", "title": "X"})

        api_client.get.assert_called_once_with("/resumes/r1")
        assert result == ""

    def test_analyze_resume_light_returns_empty_on_value_error(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's new ``ValueError`` arm at
        # relevance_handler.py:145.
        db = Database(temp_db_path)
        api_client = MagicMock()
        api_client.get.side_effect = ValueError("bad response")
        handler = RelevanceHandler(database=db, api_client=api_client)

        result = handler.analyze_resume_light({"id": "r1", "title": "X"})

        api_client.get.assert_called_once_with("/resumes/r1")
        assert result == ""


class TestRelevanceHandlerIsSuitableHeavyErrors:
    """``RelevanceHandler.is_suitable_heavy`` must still complete (and
    call the AI) when the ``api_client.get`` for the full vacancy
    fails (PR #111 added
    ``except (requests.RequestException, ValueError)`` at line 221).
    """

    def test_is_suitable_heavy_completes_on_request_exception(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's new clause at
        # relevance_handler.py:221.
        db = Database(temp_db_path)
        api_client = MagicMock()
        api_client.get.side_effect = requests.RequestException("network down")
        ai_client = MagicMock()
        ai_client.complete.return_value = (
            '{"suitable": true, "score": 80, "reason": "good match"}'
        )
        handler = RelevanceHandler(
            database=db,
            api_client=api_client,
            ai_client=ai_client,
        )

        result = handler.is_suitable_heavy({"id": 1, "name": "Senior Python"})

        # The heavy check completed (no crash) and the AI was still
        # called even though the full vacancy fetch failed.
        assert result.suitable is True
        assert result.score == 80
        ai_client.complete.assert_called_once()
        # The prompt must NOT contain "Описание:" because full_vacancy
        # fetch failed; the prompt is built from name only.
        prompt = ai_client.complete.call_args[0][0]
        assert "Описание:" not in prompt

    def test_is_suitable_heavy_completes_on_value_error(
        self, temp_db_path: str
    ) -> None:
        # Regression for PR #111's new ``ValueError`` arm at
        # relevance_handler.py:221.
        db = Database(temp_db_path)
        api_client = MagicMock()
        api_client.get.side_effect = ValueError("bad response")
        ai_client = MagicMock()
        ai_client.complete.return_value = (
            '{"suitable": false, "score": 30, "reason": "no match"}'
        )
        handler = RelevanceHandler(
            database=db,
            api_client=api_client,
            ai_client=ai_client,
        )

        result = handler.is_suitable_heavy({"id": 1, "name": "Senior Python"})

        # Strict-ish assertion: AI was called and result is the AI's
        # verdict, not a default "suitable" fallback. The fallback
        # path (max_retries / AIError) is covered elsewhere; here we
        # only verify the API call layer is decoupled from the AI
        # layer via the new exception clause.
        assert result.suitable is False
        assert result.score == 30
        ai_client.complete.assert_called_once()
