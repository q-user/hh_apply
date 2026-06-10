"""Port for search profile operations - used by other slices."""

from __future__ import annotations

from typing import Protocol

from job_bot.vacancy_search.models.search_profile import (
    SearchProfile,
    SearchProfileCreate,
    SearchProfileUpdate,
)


class SearchProfilePort(Protocol):
    """Port for search profile operations."""

    def create_profile(self, profile: SearchProfileCreate) -> SearchProfile:
        """Create a new search profile."""
        ...

    def get_profile(self, profile_id: str) -> SearchProfile | None:
        """Get a search profile by ID."""
        ...

    def get_profile_by_name(self, name: str) -> SearchProfile | None:
        """Get a search profile by name."""
        ...

    def list_profiles(self, active_only: bool = False) -> list[SearchProfile]:
        """List all search profiles."""
        ...

    def update_profile(
        self, profile_id: str, update: SearchProfileUpdate
    ) -> SearchProfile | None:
        """Update a search profile."""
        ...

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a search profile."""
        ...
