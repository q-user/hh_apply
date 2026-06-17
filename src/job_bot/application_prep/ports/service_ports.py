"""Port Protocols for the 4 per-phase services (issue #147).

Each of the 4 services in :mod:`job_bot.application_prep.services` has
a Protocol here so the slimmed legacy
:class:`hh_applicant_tool.application.use_cases.prepare_vacancies.PrepareVacanciesUseCase`
(and any other consumer) can depend on the abstract service surface
rather than on the concrete classes. The concrete classes are
duck-typed on ``storage`` / ``api_client`` / ``relevance_obj`` (i.e.
they accept the legacy ``hh_applicant_tool.storage.facade.StorageFacade``
or a VSA ``StorageFacade`` interchangeably), so the Protocols don't
spell out those dependencies.

The Protocols intentionally don't pin the parameter types to a
specific facade or API client class — the use case passes the same
references (legacy or VSA) to every method.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Callable, Protocol


class ProfileLoaderPort(Protocol):
    """Port for the :class:`ProfileLoaderService`.

    Loads the per-run input set: search profiles (single explicit id
    or all enabled ones) and the published resumes from
    ``GET /resumes/mine``.
    """

    def load_profiles(self, profile_id: str | None) -> list[Any]:
        """Return the search profiles to process.

        - ``profile_id`` is set: return ``[storage.search_profiles.get(profile_id)]``
          if the profile exists, else ``[]``. A disabled profile is
          still returned when explicitly requested (a warning is
          emitted via :meth:`notify`).
        - ``profile_id`` is ``None``: return
          ``list(storage.search_profiles.find_enabled())``.
        """
        ...

    def fetch_published_resumes(
        self, *, dry_run: bool = False
    ) -> list[dict[str, Any]]:
        """Fetch ``GET /resumes/mine`` and return the published ones.

        Side effect: persists the full batch to ``storage.resumes``
        (so the user can see drafts) unless ``dry_run=True``.
        """
        ...


class VacancyIterationPort(Protocol):
    """Port for the :class:`VacancyIterationService`.

    Owns the paginated vacancy search loop, the per-vacancy skip
    policy, the safe full-vacancy fetch, and the search/full merge.
    """

    def search_vacancies(
        self,
        search_params: dict[str, Any],
        *,
        per_page: int,
        total_pages: int,
        resume_id: str | None,
    ) -> Iterator[dict[str, Any]]:
        """Yield vacancies by paginating ``/vacancies`` (when ``text`` is
        in ``search_params``) or ``/resumes/{resume_id}/similar_vacancies``.
        """
        ...

    def skip_reason(
        self, vacancy: dict[str, Any], resume_id: str | None
    ) -> str | None:
        """Return a skip reason string or ``None``."""
        ...

    def fetch_full_vacancy(self, vacancy_id: Any) -> dict[str, Any] | None:
        """Fetch the full vacancy dict from ``GET /vacancies/{id}``;
        return ``None`` when ``vacancy_id`` is falsy or the API call
        raises (logged at DEBUG).
        """
        ...

    def merge_vacancy(
        self,
        search_vacancy: dict[str, Any],
        full_vacancy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Merge the search result with the full vacancy dict.

        ``full_vacancy`` wins for fields it has (typically
        ``description`` / ``key_skills`` / ``employer`` / etc.).
        The search result fills in the response-state keys the
        full payload doesn't have.
        """
        ...


class AiFilterPort(Protocol):
    """Port for the :class:`AiFilterService`.

    Builds the per-profile AI filter client and injects it via
    ``relevance_obj.ai_client`` using the existing
    :func:`job_bot.application_prep.utils.build_filter_ai_client`
    helper. Stateless and pure (no DB).
    """

    def build(
        self,
        *,
        profile: Any,
        resume: dict[str, Any],
        relevance_obj: Any,
        factory: Callable[[str], Any] | None,
        rate_limit: Any = None,
    ) -> Any:
        """Build the per-profile AI client and inject it via
        ``relevance_obj.ai_client``.

        Returns the AI client produced by ``factory``, or ``None`` if
        no filter is needed / available / the factory raised.
        """
        ...


class DraftPersisterPort(Protocol):
    """Port for the :class:`DraftPersisterService`.

    Owns the write-side persistence helpers (vacancy + contacts,
    employer, AI-rejected skip row, VSA→legacy draft shim).
    """

    def save_vacancy(self, vacancy: dict[str, Any]) -> None:
        """Persist a vacancy (and its contacts when present) to
        ``storage.vacancies`` / ``storage.vacancy_contacts``."""
        ...

    def save_employer(
        self, vacancy: dict[str, Any], *, api_client: Any
    ) -> None:
        """Fetch ``/employers/{id}`` and persist to ``storage.employers``."""
        ...

    def save_skipped_ai_rejected(
        self, vacancy: dict[str, Any], resume_id: str | None
    ) -> None:
        """Persist a ``skipped_vacancies`` row for an AI-rejected vacancy."""
        ...

    def save_vsa_draft_to_legacy_storage(
        self, vsa_draft: Any, resume: dict[str, Any]
    ) -> Any:
        """Mirror a VSA :class:`ApplicationDraft` to the legacy
        :class:`hh_applicant_tool.storage.facade.StorageFacade`.

        TODO(#158): remove when hh_applicant_tool is gone.
        """
        ...


__all__ = [
    "ProfileLoaderPort",
    "VacancyIterationPort",
    "AiFilterPort",
    "DraftPersisterPort",
]
