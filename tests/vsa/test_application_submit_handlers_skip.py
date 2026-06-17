"""Tests for SkipHandler (issue #145).

The handler uses the new VSA :class:`StorageFacade` (15-repo from
PR #161) to persist ``skipped_vacancies`` rows. The tests use the
``storage_conn`` fixture (in-memory SQLite with the canonical
schema) to build a real :class:`StorageFacade` and exercise the
handler against a real database.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from job_bot.application_submit.models.command import ApplyToVacanciesCommand
from job_bot.application_submit.handlers.skip_handler import SkipHandler
from job_bot.shared.storage.facade import StorageFacade


def _make_facade(conn: sqlite3.Connection) -> StorageFacade:
    """Build a VSA :class:`StorageFacade` over the test connection.

    The VSA facade lazily opens its own long-lived connection
    (``_legacy_conn``) on first access. Re-using the same
    ``StorageFacade`` instance ensures both reads and writes see
    the same connection.
    """
    return StorageFacade.from_db_path(":memory:").__class__(
        database=type(StorageFacade.from_db_path(":memory:").database)(
            ":memory:"
        )
    )


# ─── check: do_apply=False ────────────────────────────────────────────


class TestSkipHandlerDoApply:
    """``do_apply=False`` short-circuits to ``"limit_reached"``."""

    def test_do_apply_false_returns_limit_reached(self, storage_conn) -> None:
        from job_bot._legacy_compat.storage.utils import init_db

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        handler = SkipHandler(storage=facade, api_client=MagicMock())
        vacancy = {"id": 1, "name": "V"}
        resume = {"id": "r1"}
        command = ApplyToVacanciesCommand()
        reason = handler.check(
            vacancy,
            resume,
            do_apply=False,
            command=command,
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason == "limit_reached"


# ─── check: relations / archived / tests / redirects ─────────────────


class TestSkipHandlerBasicChecks:
    """Each early-out check returns a distinct reason string."""

    @pytest.fixture
    def handler(self, storage_conn):
        from job_bot._legacy_compat.storage.utils import init_db

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        return SkipHandler(storage=facade, api_client=MagicMock())

    def test_relations_returns_already_responded(self, handler) -> None:
        reason = handler.check(
            {"id": 1, "name": "V", "relations": ["responded"]},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(),
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason == "already_responded"

    def test_archived_returns_archived(self, handler) -> None:
        reason = handler.check(
            {"id": 1, "name": "V", "archived": True},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(),
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason == "archived"

    def test_has_test_with_skip_tests_returns_has_test(self, handler) -> None:
        reason = handler.check(
            {"id": 1, "name": "V", "has_test": True},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(skip_tests=True),
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason == "has_test"

    def test_has_test_without_skip_tests_passes(self, handler) -> None:
        """When ``skip_tests`` is False, ``has_test`` vacancies pass
        the check (the apply loop later routes them to the test
        handler)."""
        reason = handler.check(
            {"id": 1, "name": "V", "has_test": True},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(skip_tests=False),
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason is None

    def test_response_url_returns_redirected(self, handler) -> None:
        reason = handler.check(
            {"id": 1, "name": "V", "response_url": "https://example.com/apply"},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(),
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason == "redirected"

    def test_clean_vacancy_returns_none(self, handler) -> None:
        reason = handler.check(
            {"id": 1, "name": "Backend"},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(),
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason is None


# ─── check: excluded filter (regex) ──────────────────────────────────


class TestSkipHandlerExcludedFilter:
    """``excluded_filter`` regex matching saves the vacancy and adds it
    to the blacklist (PUT /vacancies/blacklisted/{id})."""

    def test_excluded_filter_saves_and_blacklists(self, storage_conn) -> None:
        from job_bot._legacy_compat.storage.utils import init_db

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        api_client = MagicMock()
        handler = SkipHandler(storage=facade, api_client=api_client)

        vacancy = {
            "id": 1,
            "name": "Senior Java",
            "snippet": {
                "requirement": "Java + Spring",
                "responsibility": "Backend development",
            },
            "employer": {"id": 42, "name": "Acme"},
            "alternate_url": "https://hh.ru/vacancy/1",
        }
        command = ApplyToVacanciesCommand(excluded_filter=r"\bjava\b")
        reason = handler.check(
            vacancy,
            {"id": "r1"},
            do_apply=True,
            command=command,
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason == "excluded"
        # Blacklist PUT was called.
        api_client.put.assert_called_once_with("/vacancies/blacklisted/1")
        # Skipped row was saved.
        rows = list(facade.skipped_vacancies.find(vacancy_id=1))
        assert len(rows) == 1
        assert rows[0].reason == "excluded_filter"
        assert rows[0].resume_id == "r1"

    def test_excluded_filter_blacklist_failure_does_not_break(
        self, storage_conn
    ) -> None:
        """If the PUT /vacancies/blacklisted/{id} call fails, the
        skip still returns ``"excluded"`` (the loop must not crash)."""
        from job_bot._legacy_compat.storage.utils import init_db

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        api_client = MagicMock()
        api_client.put.side_effect = RuntimeError("hh down")
        handler = SkipHandler(storage=facade, api_client=api_client)

        vacancy = {
            "id": 1,
            "name": "Senior Java",
            "snippet": {"requirement": "Java", "responsibility": ""},
        }
        reason = handler.check(
            vacancy,
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(excluded_filter=r"\bjava\b"),
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason == "excluded"

    def test_no_excluded_filter_skips_check(self, storage_conn) -> None:
        """``excluded_filter=None`` means the regex check is bypassed."""
        from job_bot._legacy_compat.storage.utils import init_db

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        api_client = MagicMock()
        handler = SkipHandler(storage=facade, api_client=api_client)

        vacancy = {
            "id": 1,
            "name": "Senior Java",
            "snippet": {"requirement": "Java", "responsibility": ""},
        }
        reason = handler.check(
            vacancy,
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(excluded_filter=None),
            relevance_handler=MagicMock(),
            vacancy_filter_ai=None,
        )
        assert reason is None
        api_client.put.assert_not_called()


# ─── check: AI rejection ──────────────────────────────────────────────


class TestSkipHandlerAiRejection:
    """When ``command.ai_filter`` is set and the per-resume AI client
    rejects a vacancy, the handler returns ``"ai_rejected"`` and saves
    the skip. Already-rejected vacancies return
    ``"ai_already_skipped"``."""

    @pytest.fixture
    def handler(self, storage_conn):
        from job_bot._legacy_compat.storage.utils import init_db

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        return SkipHandler(storage=facade, api_client=MagicMock())

    def test_ai_rejected_returns_reason_and_saves(
        self, handler, storage_conn
    ) -> None:
        from job_bot.shared.storage.facade import StorageFacade

        relevance = MagicMock()
        relevance.is_suitable_heavy.return_value = MagicMock(suitable=False)

        reason = handler.check(
            {"id": 7, "name": "V"},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(ai_filter="heavy"),
            relevance_handler=relevance,
            vacancy_filter_ai=MagicMock(),
        )
        assert reason == "ai_rejected"
        relevance.is_suitable_heavy.assert_called_once()
        facade = StorageFacade(storage_conn)
        rows = list(facade.skipped_vacancies.find(vacancy_id=7))
        assert len(rows) == 1
        assert rows[0].reason == "ai_rejected"

    def test_ai_accepted_returns_none(self, handler) -> None:
        relevance = MagicMock()
        relevance.is_suitable_heavy.return_value = MagicMock(suitable=True)

        reason = handler.check(
            {"id": 8, "name": "V"},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(ai_filter="heavy"),
            relevance_handler=relevance,
            vacancy_filter_ai=MagicMock(),
        )
        assert reason is None

    def test_ai_filter_disabled_skips_ai_check(self, handler) -> None:
        """When ``command.ai_filter`` is ``None``, the AI path is
        bypassed even if a ``vacancy_filter_ai`` is supplied."""
        relevance = MagicMock()

        reason = handler.check(
            {"id": 9, "name": "V"},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(ai_filter=None),
            relevance_handler=relevance,
            vacancy_filter_ai=MagicMock(),
        )
        assert reason is None
        relevance.is_suitable_heavy.assert_not_called()
        relevance.is_suitable_light.assert_not_called()

    def test_ai_filter_set_but_no_client_skips_ai_check(self, handler) -> None:
        """When ``command.ai_filter`` is set but no
        ``vacancy_filter_ai`` is supplied, the AI check is bypassed
        (the apply loop ran without configuring the AI client for
        this resume)."""
        relevance = MagicMock()

        reason = handler.check(
            {"id": 10, "name": "V"},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(ai_filter="heavy"),
            relevance_handler=relevance,
            vacancy_filter_ai=None,
        )
        assert reason is None
        relevance.is_suitable_heavy.assert_not_called()

    def test_ai_already_skipped_returns_reason(
        self, handler, storage_conn
    ) -> None:
        """A vacancy that was previously AI-rejected is short-circuited."""
        from job_bot._legacy_compat.storage.utils import init_db
        from job_bot.shared.storage.facade import StorageFacade

        init_db(storage_conn)
        # Persist a previous AI-rejection.
        facade = StorageFacade(storage_conn)
        handler.save_skipped({"id": 11, "name": "V"}, "ai_rejected", "r1")
        facade.skipped_vacancies.commit()

        relevance = MagicMock()

        reason = handler.check(
            {"id": 11, "name": "V"},
            {"id": "r1"},
            do_apply=True,
            command=ApplyToVacanciesCommand(ai_filter="heavy"),
            relevance_handler=relevance,
            vacancy_filter_ai=MagicMock(),
        )
        assert reason == "ai_already_skipped"
        # AI was not called because the skip was short-circuited.
        relevance.is_suitable_heavy.assert_not_called()


# ─── is_already_skipped ──────────────────────────────────────────────


class TestSkipHandlerIsAlreadySkipped:
    """Direct test of the :meth:`is_already_skipped` helper."""

    def test_returns_true_when_previously_saved(self, storage_conn) -> None:
        from job_bot._legacy_compat.storage.utils import init_db

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        handler = SkipHandler(storage=facade, api_client=MagicMock())
        handler.save_skipped({"id": 1, "name": "V"}, "ai_rejected", "r1")
        facade.skipped_vacancies.commit()
        assert handler.is_already_skipped({"id": 1}, resume_id="r1") is True

    def test_returns_false_when_not_saved(self, storage_conn) -> None:
        from job_bot._legacy_compat.storage.utils import init_db

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        handler = SkipHandler(storage=facade, api_client=MagicMock())
        assert handler.is_already_skipped({"id": 99}, resume_id="r1") is False

    def test_returns_true_when_saved_for_other_resume(
        self, storage_conn
    ) -> None:
        """A skip saved for resume_id="" (account-wide blacklist) is
        matched by ``is_already_skipped`` regardless of the per-resume
        query."""
        from job_bot._legacy_compat.storage.utils import init_db

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        handler = SkipHandler(storage=facade, api_client=MagicMock())
        handler.save_skipped({"id": 1, "name": "V"}, "excluded_filter", "")
        facade.skipped_vacancies.commit()
        assert handler.is_already_skipped({"id": 1}, resume_id="r1") is True


# ─── save_skipped ─────────────────────────────────────────────────────


class TestSkipHandlerSaveSkipped:
    """Direct test of the :meth:`save_skipped` helper."""

    def test_save_persists_row(self, storage_conn) -> None:
        from job_bot._legacy_compat.storage.utils import init_db
        from job_bot.shared.storage.facade import StorageFacade

        init_db(storage_conn)
        facade = StorageFacade(storage_conn)
        handler = SkipHandler(storage=facade, api_client=MagicMock())
        handler.save_skipped(
            {
                "id": 1,
                "name": "Senior",
                "employer": {"name": "Acme"},
                "alternate_url": "https://hh.ru/vacancy/1",
            },
            "excluded_filter",
            "r1",
        )
        facade.skipped_vacancies.commit()
        rows = list(facade.skipped_vacancies.find(vacancy_id=1))
        assert len(rows) == 1
        assert rows[0].resume_id == "r1"
        assert rows[0].reason == "excluded_filter"
        assert rows[0].name == "Senior"
        assert rows[0].employer_name == "Acme"
        assert rows[0].alternate_url == "https://hh.ru/vacancy/1"


# ─── Protocol satisfaction ────────────────────────────────────────────


def test_skip_handler_satisfies_skip_port() -> None:
    from job_bot.application_submit.ports.skip_port import SkipPort

    handler: SkipPort = SkipHandler(storage=MagicMock(), api_client=MagicMock())
    assert callable(handler.check)
    assert callable(handler.is_already_skipped)
    assert callable(handler.save_skipped)
