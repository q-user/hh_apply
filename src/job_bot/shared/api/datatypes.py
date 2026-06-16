"""TypedDicts for HH.ru API responses (VSA port of
``hh_applicant_tool.api.datatypes``, issue #152).

Each TypedDict has a sibling ``to_internal_<name>`` adapter that maps
the wire format to the matching internal dataclass (or returns the
TypedDict as-is when no internal model exists).
"""

from __future__ import annotations

from typing import Any, Generic, List, Literal, Optional, TypedDict, TypeVar

# Lazy import helper: the only TypedDict with a slice-owned dataclass
# is ``SearchVacancy`` (-> ``Vacancy``). Importing ``vacancy_search``
# at module load time would invert the VSA dependency graph (the
# shared kernel must not depend on a slice), so we resolve the model
# on first use.


def _to_vacancy(data: "SearchVacancy") -> Any:
    """Resolve the slice-owned ``Vacancy`` dataclass lazily."""
    from job_bot.vacancy_search.models.vacancy import Vacancy

    # The wire format is structurally a ``dict[str, Any]``; TypedDict is
    # just a marker at type-check time.
    return Vacancy.from_hh_api(dict(data))


NegotiationStateId = Literal[
    "discard",  # отказ
    "interview",  # собес
    "response",  # отклик
    "invitation",  # приглашение
    "hired",  # выход на работу
]


class AccessToken(TypedDict):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: Literal["bearer"]


Item = TypeVar("Item")


class PaginatedItems(TypedDict, Generic[Item]):
    items: list[Item]
    found: int
    page: int
    pages: int
    per_page: int
    # Это не все поля
    clusters: Optional[Any]
    arguments: Optional[Any]
    fixes: Optional[Any]
    suggests: Optional[Any]
    # Это выглядит как глюк. Я нейронке скармливал выхлоп апи, а она писала эти
    # типы
    alternate_url: str


class IdName(TypedDict):
    id: str
    name: str


class Snippet(TypedDict):
    requirement: Optional[str]
    responsibility: Optional[str]


class ManagerActivity(TypedDict):
    last_activity_at: str


Salary = TypedDict(
    "Salary",
    {
        "from": Optional[int],
        "to": Optional[int],
        "currency": str,
        "gross": bool,
    },
)

SalaryRange = TypedDict(
    "SalaryRange",
    {
        "from": Optional[int],
        "to": Optional[int],
        "currency": str,
        "gross": bool,
        "mode": IdName,
        "frequency": IdName,
    },
)


LogoUrls = TypedDict(
    "LogoUrls",
    {
        "original": str,
        "90": str,
        "240": str,
    },
)


class EmployerShort(TypedDict):
    id: str
    name: str
    url: str
    alternate_url: str
    logo_urls: Optional[LogoUrls]
    vacancies_url: str
    accredited_it_employer: bool
    trusted: bool


class SearchEmployer(EmployerShort):
    country_id: Optional[int]


class NegotiationEmployer(EmployerShort):
    pass


class VacancyShort(TypedDict):
    id: str
    premium: bool
    name: str
    department: Optional[dict[str, Any]]
    has_test: bool
    # HH API fields
    response_letter_required: bool
    area: IdName
    salary: Optional[Salary]
    salary_range: Optional[SalaryRange]
    type: IdName
    address: Optional[dict[str, Any]]
    response_url: Optional[str]
    sort_point_distance: Optional[float]
    published_at: str
    created_at: str
    archived: bool
    apply_alternate_url: str
    show_contacts: bool
    benefits: List[Any]
    insider_interview: Optional[dict[str, Any]]
    url: str
    alternate_url: str
    professional_roles: List[IdName]


class NegotiationVacancy(VacancyShort):
    employer: NegotiationEmployer
    show_logo_in_search: Optional[bool]


class Phone(TypedDict):
    country: str
    city: str
    number: str
    formatted: str
    comment: Optional[str]


class ContactData(TypedDict):
    name: Optional[str]
    email: Optional[str]
    phones: List[Phone]
    call_tracking_enabled: bool


class SearchVacancy(VacancyShort):
    employer: SearchEmployer
    relations: List[Any]
    experimental_modes: List[str]
    manager_activity: Optional[ManagerActivity]
    snippet: Snippet
    contacts: ContactData
    schedule: IdName
    working_days: List[Any]
    working_time_intervals: List[Any]
    working_time_modes: List[Any]
    accept_temporary: bool
    fly_in_fly_out_duration: List[Any]
    work_format: List[IdName]
    working_hours: List[IdName]
    work_schedule_by_days: List[IdName]
    accept_labor_contract: bool
    civil_law_contracts: List[Any]
    night_shifts: bool
    accept_incomplete_resumes: bool
    experience: IdName
    employment: IdName
    employment_form: IdName
    internship: bool
    adv_response_url: Optional[str]
    is_adv_vacancy: bool
    adv_context: Optional[dict[str, Any]]
    allow_chat_with_manager: bool
    key_skills: Optional[List[dict[str, Any]]]


class ResumeShort(TypedDict):
    id: str
    title: str
    url: str
    alternate_url: str


class ResumeCounters(TypedDict):
    total_views: int
    new_views: int
    invitations: int
    new_invitations: int


class Resume(ResumeShort):
    status: IdName
    created_at: str
    updated_at: str
    can_publish_or_update: bool
    counters: ResumeCounters


class UserCounters(TypedDict):
    resumes_count: int
    new_resume_views: int
    unread_negotiations: int
    # ... and more


class User(TypedDict):
    id: int
    first_name: str
    last_name: str
    middle_name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    is_applicant: bool
    is_employer: bool
    is_admin: bool
    is_anonymous: bool
    is_application: bool
    counters: UserCounters
    # ... and more


class Message(TypedDict):
    id: str
    text: str
    author: dict[
        str, Any
    ]  # Could be more specific, e.g. Participant(TypedDict)
    created_at: str
    viewed_by_opponent: bool


class Counters(TypedDict):
    messages: int
    unread_messages: int


class ChatStates(TypedDict):
    # response_reminder_state: {"allowed": bool}
    response_reminder_state: dict[str, bool]


class NegotiaionState(IdName):
    # NOTE: typo preserved verbatim from the legacy module (``Negotiaion``,
    # not ``Negotiation``). The original overwrites the ``id: str`` field
    # with the stricter ``NegotiationStateId`` literal; that pre-existing
    # behaviour is preserved here.
    id: NegotiationStateId  # type: ignore[misc]


class Negotiation(TypedDict):
    id: str
    state: IdName
    created_at: str
    updated_at: str
    resume: ResumeShort
    viewed_by_opponent: bool
    has_updates: bool
    messages_url: str
    url: str
    counters: Counters
    chat_states: ChatStates
    source: str
    chat_id: int
    messaging_status: str
    decline_allowed: bool
    read: bool
    has_new_messages: bool
    applicant_question_state: bool
    hidden: bool
    vacancy: NegotiationVacancy
    tags: List[Any]


class EmployerApplicantServices(TypedDict):
    target_employer: dict[str, int]


class Employer(EmployerShort):
    has_divisions: bool
    type: str
    description: Optional[str]
    site_url: str
    relations: List[Any]
    area: IdName
    country_code: str
    industries: List[Any]
    is_identified_by_esia: bool
    badges: List[Any]
    branded_description: Optional[str]
    branding: Optional[dict[str, Any]]
    insider_interviews: List[Any]
    open_vacancies: int
    applicant_services: EmployerApplicantServices


# ─── Internal adapters ───────────────────────────────────────────
#
# For every TypedDict above, a ``to_internal_<Name>`` adapter maps the
# wire format to the corresponding internal dataclass (or returns the
# TypedDict as-is when no internal model exists).
#
# The pattern is explicit per-class on purpose: TypedDicts don't
# survive ``isinstance`` checks so a single ``to_internal(obj)``
# dispatcher would have to ``type()``-switch, which is brittle and
# hard to grep for at call sites.


def to_internal_negotiationstateid(d: NegotiationStateId) -> NegotiationStateId:
    """Passthrough — the literal has no internal model."""
    return d


def to_internal_accesstoken(d: AccessToken) -> AccessToken:
    """Passthrough — no internal model exists yet."""
    return d


def to_internal_paginateditems(
    d: "PaginatedItems[Any]",
) -> "PaginatedItems[Any]":
    """Passthrough — caller decides the element type."""
    return d


def to_internal_idname(d: IdName) -> IdName:
    """Passthrough — ``IdName`` is a leaf shape."""
    return d


def to_internal_snippet(d: Snippet) -> Snippet:
    """Passthrough."""
    return d


def to_internal_manageractivity(d: ManagerActivity) -> ManagerActivity:
    """Passthrough."""
    return d


def to_internal_salary(d: Salary) -> Salary:
    """Passthrough."""
    return d


def to_internal_salaryrange(d: SalaryRange) -> SalaryRange:
    """Passthrough."""
    return d


def to_internal_logourls(d: LogoUrls) -> LogoUrls:
    """Passthrough."""
    return d


def to_internal_employershort(d: EmployerShort) -> EmployerShort:
    """Passthrough."""
    return d


def to_internal_searchemployer(d: SearchEmployer) -> SearchEmployer:
    """Passthrough."""
    return d


def to_internal_negotiationemployer(
    d: NegotiationEmployer,
) -> NegotiationEmployer:
    """Passthrough."""
    return d


def to_internal_vacancyshort(d: VacancyShort) -> VacancyShort:
    """Passthrough — :class:`Vacancy` (in ``vacancy_search.models``) is the
    richer dataclass and uses its own ``from_hh_api`` factory; callers that
    want the domain object can call :func:`to_internal_search_vacancy`."""
    return d


def to_internal_negotiationvacancy(d: NegotiationVacancy) -> NegotiationVacancy:
    """Passthrough."""
    return d


def to_internal_phone(d: Phone) -> Phone:
    """Passthrough."""
    return d


def to_internal_contactdata(d: ContactData) -> ContactData:
    """Passthrough."""
    return d


def to_internal_searchvacancy(d: SearchVacancy) -> Any:
    """Map a ``SearchVacancy`` payload to the ``Vacancy`` dataclass.

    The :class:`Vacancy` dataclass lives in the
    :mod:`job_bot.vacancy_search.models` slice, so the import is
    deferred to call time to keep the shared kernel free of slice
    dependencies.
    """
    return _to_vacancy(d)


def to_internal_resumeshort(d: ResumeShort) -> ResumeShort:
    """Passthrough."""
    return d


def to_internal_resumecounters(d: ResumeCounters) -> ResumeCounters:
    """Passthrough."""
    return d


def to_internal_resume(d: Resume) -> Resume:
    """Passthrough — no slice-owned ``Resume`` dataclass yet."""
    return d


def to_internal_usercounters(d: UserCounters) -> UserCounters:
    """Passthrough."""
    return d


def to_internal_user(d: User) -> User:
    """Passthrough — ``UserProfile`` is a *config* model, not a 1:1 of
    the HH API user response, so it cannot serve as the internal
    mapping for this TypedDict."""
    return d


def to_internal_message(d: Message) -> Message:
    """Passthrough."""
    return d


def to_internal_counters(d: Counters) -> Counters:
    """Passthrough."""
    return d


def to_internal_chatstates(d: ChatStates) -> ChatStates:
    """Passthrough."""
    return d


def to_internal_negotiaionstate(d: NegotiaionState) -> NegotiaionState:
    """Passthrough — note the typo in the TypedDict name, preserved verbatim."""
    return d


def to_internal_negotiation(d: Negotiation) -> Negotiation:
    """Passthrough."""
    return d


def to_internal_employerapplicantservices(
    d: EmployerApplicantServices,
) -> EmployerApplicantServices:
    """Passthrough."""
    return d


def to_internal_employer(d: Employer) -> Employer:
    """Passthrough."""
    return d


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
    # Adapters
    "to_internal_accesstoken",
    "to_internal_chatstates",
    "to_internal_contactdata",
    "to_internal_counters",
    "to_internal_employer",
    "to_internal_employerapplicantservices",
    "to_internal_employershort",
    "to_internal_idname",
    "to_internal_logourls",
    "to_internal_manageractivity",
    "to_internal_message",
    "to_internal_negotiaionstate",
    "to_internal_negotiation",
    "to_internal_negotiationemployer",
    "to_internal_negotiationstateid",
    "to_internal_negotiationvacancy",
    "to_internal_paginateditems",
    "to_internal_phone",
    "to_internal_resume",
    "to_internal_resumecounters",
    "to_internal_resumeshort",
    "to_internal_salary",
    "to_internal_salaryrange",
    "to_internal_searchemployer",
    "to_internal_searchvacancy",
    "to_internal_snippet",
    "to_internal_user",
    "to_internal_usercounters",
    "to_internal_vacancyshort",
)
