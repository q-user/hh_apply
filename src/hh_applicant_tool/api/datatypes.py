"""Legacy ``hh_applicant_tool.api.datatypes`` shim — DEPRECATED (issue #152).

All TypedDicts have been moved to :mod:`job_bot.shared.api.datatypes`.
This module is preserved as a deprecation shim that re-exports the
public surface so legacy call sites keep working for one release
window. New code should depend on the VSA module directly.
"""

from __future__ import annotations

import warnings

from job_bot.shared.api.datatypes import (  # noqa: F401  (re-export)
    AccessToken,
    ChatStates,
    ContactData,
    Counters,
    Employer,
    EmployerApplicantServices,
    EmployerShort,
    IdName,
    Item,
    LogoUrls,
    ManagerActivity,
    Message,
    NegotiaionState,  # NOTE: typo preserved verbatim from the legacy module.
    Negotiation,
    NegotiationEmployer,
    NegotiationStateId,
    NegotiationVacancy,
    PaginatedItems,
    Phone,
    Resume,
    ResumeCounters,
    ResumeShort,
    Salary,
    SalaryRange,
    SearchEmployer,
    SearchVacancy,
    Snippet,
    User,
    UserCounters,
    VacancyShort,
)

warnings.warn(
    "hh_applicant_tool.api.datatypes is deprecated; "
    "use job_bot.shared.api.datatypes instead (issue #152).",
    DeprecationWarning,
    stacklevel=2,
)


__all__ = (
    "AccessToken",
    "ChatStates",
    "ContactData",
    "Counters",
    "Employer",
    "EmployerApplicantServices",
    "EmployerShort",
    "IdName",
    "Item",
    "LogoUrls",
    "ManagerActivity",
    "Message",
    "NegotiaionState",
    "Negotiation",
    "NegotiationEmployer",
    "NegotiationStateId",
    "NegotiationVacancy",
    "PaginatedItems",
    "Phone",
    "Resume",
    "ResumeCounters",
    "ResumeShort",
    "Salary",
    "SalaryRange",
    "SearchEmployer",
    "SearchVacancy",
    "Snippet",
    "User",
    "UserCounters",
    "VacancyShort",
)
