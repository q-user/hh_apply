"""``ProfileLoaderService`` — load search profiles + published resumes (issue #147).

VSA replacement for the legacy
``PrepareVacanciesUseCase._load_profiles`` +
``PrepareVacanciesUseCase._fetch_published_resumes`` pair. The service
loads the per-run input set (search profiles + resumes) and returns
them in the shapes the orchestrator needs:

* :meth:`load_profiles` returns ``list[SearchProfileModel]`` — the
  enabled profiles (or a single explicit profile by id, including
  disabled ones when explicitly requested).
* :meth:`fetch_published_resumes` returns
  ``list[dict[str, Any]]`` — the resumes with ``status.id ==
  "published"`` from ``GET /resumes/mine``.

The service is duck-typed on ``storage``: the legacy
``hh_applicant_tool.storage.facade.StorageFacade`` exposes
``.search_profiles`` and ``.resumes`` properties that the service
uses. A VSA ``StorageFacade`` (issue #146) could be wired in once the
VSA repos expose compatible APIs (see issue #158 followup).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ProfileLoaderService:
    """Load the per-run input set for the prepare-vacancies pipeline.

    The service is duck-typed on the ``storage`` and ``api_client``
    dependencies so it can be used by both the legacy use case and
    the VSA ``ApplicationPrepSlice`` without forcing a specific
    facade implementation.

    Args:
        api_client: duck-typed HH API client. Must support
            ``.get("/resumes/mine") -> dict[str, Any]`` returning
            ``{"items": [...]}``.
        storage: duck-typed storage facade. Must support
            ``.search_profiles.get(id)`` and
            ``.search_profiles.find_enabled()`` for profile loading,
            and ``.resumes.save_batch(items)`` for resume persistence.
        progress_callback: optional ``Callable[[str], None]`` invoked
            with human-readable progress messages (matches the legacy
            use case's ``progress_callback`` contract).
    """

    def __init__(
        self,
        *,
        api_client: Any,
        storage: Any,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.api_client = api_client
        self.storage = storage
        self.progress_callback = progress_callback

    # ─── Public API ──────────────────────────────────────────────

    def load_profiles(self, profile_id: str | None) -> list[Any]:
        """Return the search profiles to process.

        - ``profile_id`` is set: return ``[storage.search_profiles.get(profile_id)]``
          if the profile exists, else ``[]``. A disabled profile is
          still returned when explicitly requested (a warning is
          emitted via :meth:`notify`).
        - ``profile_id`` is ``None``: return
          ``list(storage.search_profiles.find_enabled())``.

        Returns:
            List of search profile models. Empty list when no
            profiles match.
        """
        if profile_id:
            profile = self.storage.search_profiles.get(profile_id)
            if profile is None:
                logger.warning("Search profile %r не найден", profile_id)
                return []
            if not profile.enabled:
                logger.warning(
                    "Search profile %r выключен (enabled=False) — "
                    "обрабатываю по явному запросу",
                    profile_id,
                )
                self.notify(
                    f"⚠️ Профиль {profile_id} выключен — обрабатываю "
                    "по явному запросу"
                )
            return [profile]
        return list(self.storage.search_profiles.find_enabled())

    def fetch_published_resumes(
        self, *, dry_run: bool = False
    ) -> list[dict[str, Any]]:
        """Fetch ``/resumes/mine`` and return only the published ones.

        Side effect: persists the full batch to ``storage.resumes``
        (so the user can see drafts) unless ``dry_run=True``.

        Args:
            dry_run: skip the ``storage.resumes.save_batch`` call.

        Returns:
            Resumes with ``status.id == "published"``, in the order
            returned by the API.
        """
        from job_bot._legacy_compat.storage.repositories.errors import (
            RepositoryError,
        )

        resumes: list[dict[str, Any]] = (
            self.api_client.get("/resumes/mine").get("items") or []
        )
        if not dry_run:
            try:
                self.storage.resumes.save_batch(resumes)
            except RepositoryError as ex:
                logger.debug(ex)
        return [
            r
            for r in resumes
            if (r.get("status") or {}).get("id") == "published"
        ]

    # ─── Progress notification ──────────────────────────────────

    def notify(self, message: str) -> None:
        """Print + invoke the progress callback (matches the legacy
        ``_notify`` contract)."""
        print(message)
        if self.progress_callback is not None:
            try:
                self.progress_callback(message)
            except Exception as ex:  # noqa: BLE001
                logger.warning("progress_callback error: %s", ex)
