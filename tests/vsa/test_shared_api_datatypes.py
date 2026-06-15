"""Tests for the VSA port of ``hh_applicant_tool.api.datatypes`` to
``job_bot.shared.api.datatypes`` (issue #152).

Contract:

* Every TypedDict that lived at ``hh_applicant_tool.api.datatypes``
  must be re-exported from ``job_bot.shared.api.datatypes`` under the
  same name.
* Each TypedDict has a small ``to_internal()`` adapter; the adapter
  returns the matching internal dataclass when one exists, otherwise
  the adapter returns the TypedDict (i.e. a plain ``dict``) as-is.
* The legacy ``hh_applicant_tool.api.datatypes`` module keeps working
  for one release window and emits a single ``DeprecationWarning`` on
  import (per the canonical deprecation contract in
  ``tests/test_issue_92_deprecation.py``).
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest

# The complete inventory of TypedDicts that the legacy module exposed.
EXPECTED_TYPEDDICTS: tuple[str, ...] = (
    "NegotiationStateId",
    "AccessToken",
    "PaginatedItems",
    "IdName",
    "Snippet",
    "ManagerActivity",
    "Salary",
    "SalaryRange",
    "LogoUrls",
    "EmployerShort",
    "SearchEmployer",
    "NegotiationEmployer",
    "VacancyShort",
    "NegotiationVacancy",
    "Phone",
    "ContactData",
    "SearchVacancy",
    "ResumeShort",
    "ResumeCounters",
    "Resume",
    "UserCounters",
    "User",
    "Message",
    "Counters",
    "ChatStates",
    "NegotiaionState",  # NOTE: typo preserved verbatim from the legacy module.
    "Negotiation",
    "EmployerApplicantServices",
    "Employer",
)


# ─── New canonical location ─────────────────────────────────────


def test_new_datatypes_module_is_importable() -> None:
    """The new ``job_bot.shared.api.datatypes`` module exists."""
    module = importlib.import_module("job_bot.shared.api.datatypes")
    assert module is not None


@pytest.mark.parametrize("name", EXPECTED_TYPEDDICTS)
def test_datatypes_typeddicts_importable(name: str) -> None:
    """Every TypedDict name from the legacy module is exposed in the new one."""
    module = importlib.import_module("job_bot.shared.api.datatypes")
    assert hasattr(module, name), (
        f"job_bot.shared.api.datatypes is missing {name!r}"
    )


def test_datatypes_module_exposes_to_internal_for_every_typeddict() -> None:
    """For every public TypedDict there is a ``to_internal_<Name>`` adapter.

    The adapter naming convention is a small, easy-to-grep suffix so
    static checkers (and the canonical shim contract test) can verify
    it. We intentionally do not use a single ``to_internal(obj)``
    dispatcher — TypedDicts do not survive ``isinstance`` so the
    dispatcher would have to switch on ``type(obj)`` and that is
    brittle. Per-class adapter functions keep the call sites explicit.
    """
    module = importlib.import_module("job_bot.shared.api.datatypes")
    missing: list[str] = []
    for name in EXPECTED_TYPEDDICTS:
        adapter = f"to_internal_{name.lower()}"
        if not hasattr(module, adapter):
            missing.append(adapter)
    assert missing == [], (
        "Missing to_internal_<Name> adapters in "
        f"job_bot.shared.api.datatypes: {missing}"
    )


def test_to_internal_for_typeddict_without_internal_model_returns_dict() -> (
    None
):
    """For TypedDicts without a slice-owned dataclass, the adapter is a passthrough.

    ``User`` has no corresponding VSA dataclass (the existing
    ``UserProfile`` is a *config* model, not a 1:1 mapping of the HH
    API user response). Its ``to_internal_user`` must therefore return
    the TypedDict value (which is structurally a ``dict``).
    """
    from job_bot.shared.api.datatypes import User, to_internal_user

    payload: User = {
        "id": 1,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "middle_name": None,
        "email": "ada@example.com",
        "phone": None,
        "is_applicant": True,
        "is_employer": False,
        "is_admin": False,
        "is_anonymous": False,
        "is_application": False,
        "counters": {
            "resumes_count": 1,
            "new_resume_views": 0,
            "unread_negotiations": 0,
        },
    }
    result = to_internal_user(payload)
    assert result is payload or dict(result) == dict(payload)


def test_to_internal_for_search_vacancy_returns_vacancy_dataclass() -> None:
    """``SearchVacancy`` has a slice-owned ``Vacancy`` dataclass.

    The adapter must use the dataclass' ``from_hh_api`` factory so
    callers get the same shape as the rest of the codebase.
    """
    from job_bot.shared.api.datatypes import (
        SearchVacancy,
        to_internal_searchvacancy,
    )
    from job_bot.vacancy_search.models.vacancy import Vacancy

    payload: SearchVacancy = {
        "id": "42",
        "premium": False,
        "name": "Python developer",
        "department": None,
        "has_test": False,
        "response_letter_required": False,
        "area": {"id": "1", "name": "Moscow"},
        "salary": None,
        "salary_range": None,
        "type": {"id": "open", "name": "Open"},
        "address": None,
        "response_url": None,
        "sort_point_distance": None,
        "published_at": "2025-01-01T00:00:00+00:00",
        "created_at": "2025-01-01T00:00:00+00:00",
        "archived": False,
        "apply_alternate_url": "https://hh.ru/apply/42",
        "show_contacts": False,
        "benefits": [],
        "insider_interview": None,
        "url": "https://api.hh.ru/vacancies/42",
        "alternate_url": "https://hh.ru/vacancy/42",
        "professional_roles": [],
        "employer": {
            "id": "1",
            "name": "Acme",
            "url": "https://api.hh.ru/employers/1",
            "alternate_url": "https://hh.ru/employer/1",
            "logo_urls": None,
            "vacancies_url": "https://api.hh.ru/vacancies?employer_id=1",
            "accredited_it_employer": False,
            "trusted": True,
            "country_id": None,
        },
        "relations": [],
        "experimental_modes": [],
        "manager_activity": None,
        "snippet": {"requirement": "Python", "responsibility": "Code"},
        "contacts": {
            "name": None,
            "email": None,
            "phones": [],
            "call_tracking_enabled": False,
        },
        "schedule": {"id": "remote", "name": "Remote"},
        "working_days": [],
        "working_time_intervals": [],
        "working_time_modes": [],
        "accept_temporary": False,
        "fly_in_fly_out_duration": [],
        "work_format": [],
        "working_hours": [],
        "work_schedule_by_days": [],
        "accept_labor_contract": False,
        "civil_law_contracts": [],
        "night_shifts": False,
        "accept_incomplete_resumes": False,
        "experience": {"id": "between1And3", "name": "1-3 years"},
        "employment": {"id": "full", "name": "Full"},
        "employment_form": {"id": "full", "name": "Full"},
        "internship": False,
        "adv_response_url": None,
        "is_adv_vacancy": False,
        "adv_context": None,
        "allow_chat_with_manager": False,
        "key_skills": [],
    }
    result = to_internal_searchvacancy(payload)
    assert isinstance(result, Vacancy)
    assert result.hh_id == "42"
    assert result.name == "Python developer"
    assert result.employer_name == "Acme"


# ─── Legacy import path (one release window) ─────────────────────


def test_legacy_import_path_still_works() -> None:
    """The dotted path ``hh_applicant_tool.api.datatypes`` keeps working.

    Code outside the project (e.g. a 3rd-party fork) may still import
    from the legacy module. We preserve the public surface for one
    release window.
    """
    module = importlib.import_module("hh_applicant_tool.api.datatypes")
    assert hasattr(module, "User")
    assert hasattr(module, "Resume")
    assert hasattr(module, "SearchVacancy")


def test_legacy_import_path_uses_canonical_deprecation_warning() -> None:
    """Importing the legacy module emits a single DeprecationWarning.

    The warning message follows the contract established in
    ``tests/test_issue_92_deprecation.py``:

        "<module.path> is deprecated; use <vsa.path> instead (issue #<N>)."
    """
    sys.modules.pop("hh_applicant_tool.api.datatypes", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("hh_applicant_tool.api.datatypes")

    matches = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "hh_applicant_tool.api.datatypes" in str(w.message)
        and "job_bot.shared.api.datatypes" in str(w.message)
        and "issue #152" in str(w.message)
    ]
    assert matches, (
        "expected a DeprecationWarning for hh_applicant_tool.api.datatypes; "
        f"got: {[str(w.message) for w in caught]}"
    )


def test_legacy_datatypes_reexports_match_canonical_symbols() -> None:
    """Re-exports from the legacy path point at the same objects as the new path.

    This is the "preserve dotted-path call sites" guarantee. A
    consumer that did ``from hh_applicant_tool.api.datatypes import
    User`` must end up with the same TypedDict as one that did
    ``from job_bot.shared.api.datatypes import User``.
    """
    legacy = importlib.import_module("hh_applicant_tool.api.datatypes")
    new = importlib.import_module("job_bot.shared.api.datatypes")
    for name in (
        "User",
        "Resume",
        "SearchVacancy",
        "PaginatedItems",
        "VacancyShort",
    ):
        assert getattr(legacy, name) is getattr(new, name), (
            f"legacy {name!r} is not the same object as the new {name!r}; "
            "the dotted-path call site contract is broken"
        )
