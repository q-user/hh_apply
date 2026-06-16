"""``VacancyIterationService`` — vacancy search loop + pure orchestration (issue #147).

VSA replacement for the legacy
``PrepareVacanciesUseCase._vacancy_search_loop`` +
the pure-orchestration part of
``PrepareVacanciesUseCase._process_vacancy``.

The service owns the parts of the per-vacancy pipeline that don't
involve AI filters, cover letter generation, or draft persistence
(those stay in the orchestrator). The four exposed helpers are:

* :meth:`search_vacancies` — paginated loop over
  ``/vacancies`` (when ``text`` is in ``search_params``) or
  ``/resumes/{resume_id}/similar_vacancies``. Caps at ``total_pages``
  pages and stops when the API reports no more pages.
* :meth:`skip_reason` — applies the skip policy (``relations``,
  ``archived``, ``previously_skipped``) and returns a reason string
  or ``None``.
* :meth:`fetch_full_vacancy` — safe ``GET /vacancies/{id}`` that
  returns ``None`` on error.
* :meth:`merge_vacancy` — merges a search result with a full vacancy
  dict (``full`` wins; ``search`` fills in the response-state keys
  ``relations`` / ``has_test`` / ``response_url`` / etc. when ``full``
  doesn't have them).

The service is duck-typed on ``storage``: it needs
``.skipped_vacancies.find(resume_id=..., vacancy_id=...)``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)


class VacancyIterationService:
    """Vacancy search + pure-orchestration helpers for the prepare
    pipeline.

    The service is duck-typed on ``api_client`` and ``storage`` so it
    works with both the legacy use case and the VSA
    ``ApplicationPrepSlice``.

    Args:
        api_client: duck-typed HH API client. Must support
            ``.get(path, params=None) -> dict[str, Any]``.
        storage: optional duck-typed storage facade for the
            ``skipped_vacancies`` lookups. ``None`` is allowed but
            disables the "previously_skipped" check.
        progress_callback: optional progress callback.
    """

    def __init__(
        self,
        *,
        api_client: Any,
        storage: Any = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.api_client = api_client
        self.storage = storage
        self.progress_callback = progress_callback

    # ─── Public API ──────────────────────────────────────────────

    def search_vacancies(
        self,
        search_params: dict[str, Any],
        *,
        per_page: int,
        total_pages: int,
        resume_id: str | None,
    ) -> Iterator[dict[str, Any]]:
        """Yield vacancies by paginating ``/vacancies`` (when
        ``text`` is in ``search_params``) or
        ``/resumes/{resume_id}/similar_vacancies``.

        The loop caps at ``total_pages`` pages and stops when the
        API reports the last page (``pages=0`` in the response, or
        ``page >= pages - 1``) or returns an empty ``items`` list.

        Args:
            search_params: dict of search parameters (text,
                area, salary, etc.). ``per_page`` and
                ``total_pages`` are stripped by the caller (the
                orchestrator injects them via the keyword args).
            per_page: vacancies per page.
            total_pages: max pages to fetch.
            resume_id: required when ``text`` is absent (the
                similar-vacancies endpoint is per-resume).

        Yields:
            Vacancy dicts (raw HH API shapes, e.g. ``id``,
            ``name``, ``employer``, ``alternate_url``,
            ``relations``, ``archived``, ``has_test``, etc.).
        """
        has_text = bool(search_params.get("text"))
        for page in range(total_pages):
            params = dict(search_params)
            params["page"] = page
            params["per_page"] = per_page

            if has_text:
                endpoint = "/vacancies"
            else:
                if not resume_id:
                    raise ValueError(
                        "resume_id is required for similar_vacancies endpoint"
                    )
                endpoint = f"/resumes/{resume_id}/similar_vacancies"

            res = self.api_client.get(endpoint, params)
            items = res.get("items") or []
            if not items:
                return

            yield from items

            if page >= res.get("pages", 0) - 1:
                return

    def skip_reason(
        self, vacancy: dict[str, Any], resume_id: str | None
    ) -> str | None:
        """Return a skip reason string or ``None``.

        The policy (in priority order):
        1. ``vacancy["relations"]`` is truthy → ``"already_responded"``
        2. ``vacancy["archived"]`` is truthy → ``"archived"``
        3. ``skipped_vacancies`` has a row for ``(resume_id, vacancy.id)``
           or ``("", vacancy.id)`` → ``"previously_skipped"``
        4. otherwise ``None`` (process the vacancy).
        """
        if vacancy.get("relations"):
            return "already_responded"
        if vacancy.get("archived"):
            return "archived"
        if self._is_vacancy_already_skipped(vacancy, resume_id):
            return "previously_skipped"
        return None

    def fetch_full_vacancy(self, vacancy_id: Any) -> dict[str, Any] | None:
        """Fetch the full vacancy dict from ``GET /vacancies/{id}``.

        Returns ``None`` when ``vacancy_id`` is falsy or the API
        call raises (logged at DEBUG; matches the legacy
        ``_safe_get_full_vacancy`` contract).
        """
        if not vacancy_id:
            return None
        try:
            import requests

            from hh_applicant_tool.api.errors import ApiError, BadResponse

            result: dict[str, Any] | None = self.api_client.get(
                f"/vacancies/{vacancy_id}"
            )
            return result
        except (
            requests.RequestException,
            ApiError,
            BadResponse,
        ) as ex:
            logger.debug(
                "Не удалось получить полную вакансию %s: %s",
                vacancy_id,
                ex,
            )
            return None

    def merge_vacancy(
        self,
        search_vacancy: dict[str, Any],
        full_vacancy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Merge the search result with the full vacancy dict.

        ``full_vacancy`` wins for fields it has (typically
        ``description`` / ``key_skills`` / ``employer`` / etc.).
        The search result fills in the response-state keys the
        full payload doesn't have:

        * ``relations`` (the user's response state)
        * ``has_test``
        * ``alternate_url`` (fallback)
        * ``response_url`` (test page URL)
        * ``response_letter_required``
        """
        if not full_vacancy:
            return search_vacancy
        merged = dict(full_vacancy)
        for key in (
            "relations",
            "has_test",
            "alternate_url",
            "response_url",
            "response_letter_required",
        ):
            if key not in merged and key in search_vacancy:
                merged[key] = search_vacancy[key]
        return merged

    # ─── Private helpers ────────────────────────────────────────

    def _is_vacancy_already_skipped(
        self, vacancy: dict[str, Any], resume_id: str | None
    ) -> bool:
        """Return True if a ``skipped_vacancies`` row exists for this
        vacancy (per-resume or global)."""
        vacancy_id = vacancy.get("id")
        if vacancy_id is None:
            return False
        if self.storage is None:
            return False
        from hh_applicant_tool.storage.repositories.errors import (
            RepositoryError,
        )

        try:
            if resume_id and any(
                self.storage.skipped_vacancies.find(
                    resume_id=resume_id, vacancy_id=vacancy_id
                )
            ):
                return True
            return any(
                self.storage.skipped_vacancies.find(
                    resume_id="", vacancy_id=vacancy_id
                )
            )
        except RepositoryError:
            return False
