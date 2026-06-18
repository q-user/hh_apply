"""Tests for StorageIOHandler (issue #201).

The handler persists processed vacancies, employer profiles, and
employer site info to the VSA :class:`StorageFacade`. The tests use a
fake storage facade (duck-typed with the four repositories the handler
calls: ``vacancies``, ``vacancy_contacts``, ``employers``,
``employer_sites``) and a fake ``api_client``. No real database is
opened; side-effects are inspected via the fake.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from job_bot.application_submit.handlers.storage_io_handler import (
    StorageIOHandler,
)

# ─── Fakes ──────────────────────────────────────────────────────────────


class _FakeRepo:
    """Fake repository that records every ``save`` call."""

    def __init__(self) -> None:
        self.saved: list[Any] = []

    def save(self, payload: Any) -> None:
        self.saved.append(payload)


class _FakeStorageFacade:
    """Fake storage facade exposing the four repos used by the handler."""

    def __init__(self) -> None:
        self.vacancies = _FakeRepo()
        self.vacancy_contacts = _FakeRepo()
        self.employers = _FakeRepo()
        self.employer_sites = _FakeRepo()


class _FakeCommand:
    """Minimal command stub with a configurable ``send_email`` flag."""

    def __init__(self, send_email: bool = False) -> None:
        self.send_email = send_email


# ─── save_vacancy ──────────────────────────────────────────────────────


class TestStorageIOHandlerSaveVacancy:
    """``save_vacancy`` persists the vacancy + (optionally) its contacts."""

    def test_save_vacancy_persists_vacancy(self) -> None:
        storage = _FakeStorageFacade()
        handler = StorageIOHandler(storage=storage)
        vacancy = {"id": 1, "name": "V"}
        handler.save_vacancy(vacancy)
        assert storage.vacancies.saved == [vacancy]

    def test_save_vacancy_persists_contacts_when_present(self) -> None:
        storage = _FakeStorageFacade()
        handler = StorageIOHandler(storage=storage)
        vacancy = {"id": 1, "name": "V", "contacts": {"email": "a@b.com"}}
        handler.save_vacancy(vacancy)
        assert storage.vacancies.saved == [vacancy]
        assert storage.vacancy_contacts.saved == [vacancy]

    def test_save_vacancy_skips_contacts_when_absent(self) -> None:
        storage = _FakeStorageFacade()
        handler = StorageIOHandler(storage=storage)
        vacancy = {"id": 1, "name": "V"}  # no contacts
        handler.save_vacancy(vacancy)
        assert storage.vacancy_contacts.saved == []

    def test_save_vacancy_skips_contacts_when_empty_dict(self) -> None:
        """``vacancy.contacts={}`` is falsy and is skipped (preserves the
        legacy ``_save_vacancy_to_storage`` truthiness check)."""
        storage = _FakeStorageFacade()
        handler = StorageIOHandler(storage=storage)
        handler.save_vacancy({"id": 1, "name": "V", "contacts": {}})
        assert storage.vacancy_contacts.saved == []

    def test_save_vacancy_swallows_vacancy_repo_error(self) -> None:
        """A vacancy-repo failure logs but does not raise."""
        storage = _FakeStorageFacade()
        storage.vacancies.save = MagicMock(side_effect=RuntimeError("boom"))
        handler = StorageIOHandler(storage=storage)
        # No exception, even when the repo fails.
        handler.save_vacancy(
            {"id": 1, "name": "V", "contacts": {"email": "a@b.com"}}
        )
        # The contacts save still runs after the vacancy save failure.
        assert storage.vacancy_contacts.saved == [
            {"id": 1, "name": "V", "contacts": {"email": "a@b.com"}}
        ]

    def test_save_vacancy_swallows_contacts_repo_error(self) -> None:
        """A contacts-save failure logs but does not raise."""
        storage = _FakeStorageFacade()
        storage.vacancy_contacts.save = MagicMock(
            side_effect=RuntimeError("boom")
        )
        handler = StorageIOHandler(storage=storage)
        handler.save_vacancy(
            {"id": 1, "name": "V", "contacts": {"email": "a@b.com"}}
        )
        # No exception; the handler is best-effort.
        assert storage.vacancies.saved


# ─── load_employer_profile ─────────────────────────────────────────────


class TestStorageIOHandlerLoadEmployer:
    """``load_employer_profile`` fetches the employer and (optionally)
    parses the site for emails."""

    def test_no_employer_id_is_a_noop(self) -> None:
        storage = _FakeStorageFacade()
        api = MagicMock()
        handler = StorageIOHandler(storage=storage, api_client=api)
        handler.load_employer_profile(
            {"id": 1, "name": "V"}, set(), {}, _FakeCommand()
        )
        api.get.assert_not_called()
        assert storage.employers.saved == []

    def test_seen_employer_short_circuits(self) -> None:
        storage = _FakeStorageFacade()
        api = MagicMock()
        handler = StorageIOHandler(storage=storage, api_client=api)
        seen = {"42"}
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}}, seen, {}, _FakeCommand()
        )
        api.get.assert_not_called()
        assert storage.employers.saved == []

    def test_fetches_and_saves_employer(self) -> None:
        storage = _FakeStorageFacade()
        api = MagicMock()
        api.get.return_value = {"id": "42", "name": "Acme", "site_url": ""}
        handler = StorageIOHandler(storage=storage, api_client=api)
        seen: set[str] = set()
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}}, seen, {}, _FakeCommand()
        )
        api.get.assert_called_once_with("/employers/42")
        assert storage.employers.saved == [
            {"id": "42", "name": "Acme", "site_url": ""}
        ]

    def test_no_api_client_skips_fetch(self) -> None:
        storage = _FakeStorageFacade()
        handler = StorageIOHandler(storage=storage, api_client=None)
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}}, set(), {}, _FakeCommand()
        )
        assert storage.employers.saved == []

    def test_employer_fetch_failure_is_swallowed(self) -> None:
        storage = _FakeStorageFacade()
        api = MagicMock()
        api.get.side_effect = RuntimeError("network down")
        handler = StorageIOHandler(storage=storage, api_client=api)
        seen: set[str] = set()
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}}, seen, {}, _FakeCommand()
        )
        assert storage.employers.saved == []
        assert seen == set()  # not added on failure

    def test_save_employer_failure_is_swallowed(self) -> None:
        storage = _FakeStorageFacade()
        storage.employers.save = MagicMock(side_effect=RuntimeError("db down"))
        api = MagicMock()
        api.get.return_value = {"id": "42", "name": "Acme"}
        handler = StorageIOHandler(storage=storage, api_client=api)
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}}, set(), {}, _FakeCommand()
        )
        # No exception; the handler is best-effort.

    def test_site_parse_skipped_when_send_email_false(self) -> None:
        storage = _FakeStorageFacade()
        api = MagicMock()
        api.get.return_value = {"id": "42", "site_url": "https://acme.com"}
        site_parser = MagicMock(return_value={"emails": ["a@b.com"]})
        handler = StorageIOHandler(
            storage=storage, api_client=api, site_parser=site_parser
        )
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}},
            set(),
            {},
            _FakeCommand(send_email=False),
        )
        site_parser.assert_not_called()
        assert storage.employer_sites.saved == []

    def test_site_parse_skipped_when_no_site_url(self) -> None:
        storage = _FakeStorageFacade()
        api = MagicMock()
        api.get.return_value = {"id": "42"}  # no site_url
        site_parser = MagicMock(return_value={"emails": ["a@b.com"]})
        handler = StorageIOHandler(
            storage=storage, api_client=api, site_parser=site_parser
        )
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}},
            set(),
            {},
            _FakeCommand(send_email=True),
        )
        site_parser.assert_not_called()

    def test_site_parse_skipped_when_site_parser_none(self) -> None:
        storage = _FakeStorageFacade()
        api = MagicMock()
        api.get.return_value = {"id": "42", "site_url": "https://acme.com"}
        handler = StorageIOHandler(
            storage=storage, api_client=api, site_parser=None
        )
        site_emails: dict[str, Any] = {}
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}},
            set(),
            site_emails,
            _FakeCommand(send_email=True),
        )
        # site_emails untouched; no employer_sites save.
        assert site_emails == {}
        assert storage.employer_sites.saved == []

    def test_site_emails_populated_and_employer_sites_saved(self) -> None:
        storage = _FakeStorageFacade()
        api = MagicMock()
        api.get.return_value = {"id": "42", "site_url": "acme.com"}
        site_parser = MagicMock(
            return_value={
                "emails": ["a@b.com", "c@d.com"],
                "title": "Acme",
                "description": "Cool",
                "generator": "WP",
            }
        )
        handler = StorageIOHandler(
            storage=storage, api_client=api, site_parser=site_parser
        )
        site_emails: dict[str, Any] = {}
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}},
            set(),
            site_emails,
            _FakeCommand(send_email=True),
        )
        site_parser.assert_called_once_with("https://acme.com")
        assert site_emails["42"] == ["a@b.com", "c@d.com"]
        assert len(storage.employer_sites.saved) == 1
        saved = storage.employer_sites.saved[0]
        assert saved["site_url"] == "https://acme.com"
        assert saved["employer_id"] == "42"
        assert saved["subdomains"] == []
        assert saved["title"] == "Acme"

    def test_site_parse_failure_is_swallowed(self) -> None:
        storage = _FakeStorageFacade()
        api = MagicMock()
        api.get.return_value = {"id": "42", "site_url": "https://acme.com"}
        site_parser = MagicMock(side_effect=RuntimeError("bad html"))
        handler = StorageIOHandler(
            storage=storage, api_client=api, site_parser=site_parser
        )
        site_emails: dict[str, Any] = {}
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}},
            set(),
            site_emails,
            _FakeCommand(send_email=True),
        )
        # site_emails untouched; no employer_sites save.
        assert site_emails == {}
        assert storage.employer_sites.saved == []

    def test_site_url_without_scheme_gets_https(self) -> None:
        """Bare ``acme.com`` becomes ``https://acme.com`` before parsing."""
        storage = _FakeStorageFacade()
        api = MagicMock()
        api.get.return_value = {"id": "42", "site_url": "acme.com"}
        site_parser = MagicMock(return_value={"emails": []})
        handler = StorageIOHandler(
            storage=storage, api_client=api, site_parser=site_parser
        )
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}},
            set(),
            {},
            _FakeCommand(send_email=True),
        )
        site_parser.assert_called_once_with("https://acme.com")

    def test_save_employer_site_failure_is_swallowed(self) -> None:
        storage = _FakeStorageFacade()
        storage.employer_sites.save = MagicMock(
            side_effect=RuntimeError("db down")
        )
        api = MagicMock()
        api.get.return_value = {"id": "42", "site_url": "https://acme.com"}
        site_parser = MagicMock(return_value={"emails": ["a@b.com"]})
        handler = StorageIOHandler(
            storage=storage, api_client=api, site_parser=site_parser
        )
        # No exception; the handler is best-effort.
        handler.load_employer_profile(
            {"id": 1, "employer": {"id": "42"}},
            set(),
            {},
            _FakeCommand(send_email=True),
        )
