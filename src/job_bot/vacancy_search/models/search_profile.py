"""Search Profile domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass
class SearchProfile:
    """Search profile for vacancy search."""

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    keywords: str = ""
    schedule: list[str] = field(default_factory=list)
    experience: list[str] = field(default_factory=list)
    employment: list[str] = field(default_factory=list)
    area: list[str] = field(default_factory=list)
    salary: int | None = None
    currency: str = "RUR"
    only_with_salary: bool = False
    search_period: int = 7
    per_page: int = 100
    page: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_api_params(self) -> dict[str, Any]:
        """Convert to HH API search parameters."""
        params: dict[str, Any] = {
            "text": self.keywords,
            "per_page": self.per_page,
            "page": self.page,
            "search_period": self.search_period,
            "only_with_salary": self.only_with_salary,
        }

        if self.schedule:
            params["schedule"] = self.schedule
        if self.experience:
            params["experience"] = self.experience
        if self.employment:
            params["employment"] = self.employment
        if self.area:
            params["area"] = self.area
        if self.salary:
            params["salary"] = self.salary
            params["currency"] = self.currency

        return params


@dataclass
class SearchProfileCreate:
    """Data for creating a new search profile."""

    name: str
    keywords: str = ""
    schedule: list[str] = field(default_factory=list)
    experience: list[str] = field(default_factory=list)
    employment: list[str] = field(default_factory=list)
    area: list[str] = field(default_factory=list)
    salary: int | None = None
    currency: str = "RUR"
    only_with_salary: bool = False
    search_period: int = 7
    per_page: int = 100
    page: int = 0
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_profile(self) -> SearchProfile:
        """Convert to SearchProfile entity."""
        return SearchProfile(
            name=self.name,
            keywords=self.keywords,
            schedule=self.schedule,
            experience=self.experience,
            employment=self.employment,
            area=self.area,
            salary=self.salary,
            currency=self.currency,
            only_with_salary=self.only_with_salary,
            search_period=self.search_period,
            per_page=self.per_page,
            page=self.page,
            is_active=self.is_active,
            metadata=self.metadata,
        )


@dataclass
class SearchProfileUpdate:
    """Data for updating a search profile."""

    name: str | None = None
    keywords: str | None = None
    schedule: list[str] | None = None
    experience: list[str] | None = None
    employment: list[str] | None = None
    area: list[str] | None = None
    salary: int | None = None
    currency: str | None = None
    only_with_salary: bool | None = None
    search_period: int | None = None
    per_page: int | None = None
    page: int | None = None
    is_active: bool | None = None
    metadata: dict[str, Any] | None = None

    def apply_to(self, profile: SearchProfile) -> SearchProfile:
        """Apply updates to an existing profile."""
        if self.name is not None:
            profile.name = self.name
        if self.keywords is not None:
            profile.keywords = self.keywords
        if self.schedule is not None:
            profile.schedule = self.schedule
        if self.experience is not None:
            profile.experience = self.experience
        if self.employment is not None:
            profile.employment = self.employment
        if self.area is not None:
            profile.area = self.area
        if self.salary is not None:
            profile.salary = self.salary
        if self.currency is not None:
            profile.currency = self.currency
        if self.only_with_salary is not None:
            profile.only_with_salary = self.only_with_salary
        if self.search_period is not None:
            profile.search_period = self.search_period
        if self.per_page is not None:
            profile.per_page = self.per_page
        if self.page is not None:
            profile.page = self.page
        if self.is_active is not None:
            profile.is_active = self.is_active
        if self.metadata is not None:
            profile.metadata = self.metadata

        profile.updated_at = datetime.now()
        return profile
