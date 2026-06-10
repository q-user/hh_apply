"""Search Profile handler - business logic for search profiles."""

from __future__ import annotations

from job_bot.shared.storage.database import Database
from job_bot.vacancy_search.models.search_profile import (
    SearchProfile,
    SearchProfileCreate,
    SearchProfileUpdate,
)
from job_bot.vacancy_search.repositories.search_profile_repo import (
    SearchProfileRepository,
)


class SearchProfileHandler:
    """Handler for search profile operations."""

    def __init__(self, database: Database) -> None:
        self._repo = SearchProfileRepository(database)

    # Implementation of SearchProfilePort

    def create_profile(self, profile: SearchProfileCreate) -> SearchProfile:
        """Create a new search profile."""
        entity = profile.to_profile()
        return self._repo.create(entity)

    def get_profile(self, profile_id: str) -> SearchProfile | None:
        """Get a search profile by ID."""
        return self._repo.get_by_id(profile_id)

    def get_profile_by_name(self, name: str) -> SearchProfile | None:
        """Get a search profile by name."""
        return self._repo.get_by_name(name)

    def list_profiles(self, active_only: bool = False) -> list[SearchProfile]:
        """List all search profiles."""
        return self._repo.get_all(active_only=active_only)

    def update_profile(
        self, profile_id: str, update: SearchProfileUpdate
    ) -> SearchProfile | None:
        """Update a search profile."""
        return self._repo.apply_update(profile_id, update)

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a search profile."""
        return self._repo.delete(profile_id)
