"""Vacancy domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass
class Vacancy:
    """Vacancy from HH.ru."""

    id: str = field(default_factory=lambda: str(uuid4()))
    hh_id: str = ""
    name: str = ""
    employer_name: str = ""
    employer_id: str | None = None
    area_name: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str = "RUR"
    experience: str = ""
    employment: str = ""
    schedule: str = ""
    description: str = ""
    key_skills: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    raw_data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_hh_api(cls, data: dict[str, Any]) -> Vacancy:
        """Create Vacancy from HH API response."""
        salary = data.get("salary") or {}
        employer = data.get("employer") or {}
        area = data.get("area") or {}
        experience = data.get("experience") or {}
        employment = data.get("employment") or {}
        schedule = data.get("schedule") or {}

        return cls(
            hh_id=str(data.get("id", "")),
            name=data.get("name", ""),
            employer_name=employer.get("name", ""),
            employer_id=str(employer.get("id")) if employer.get("id") else None,
            area_name=area.get("name", ""),
            salary_from=salary.get("from"),
            salary_to=salary.get("to"),
            currency=salary.get("currency", "RUR"),
            experience=experience.get("name", ""),
            employment=employment.get("name", ""),
            schedule=schedule.get("name", ""),
            description=data.get("description", ""),
            key_skills=[s.get("name", "") for s in data.get("key_skills", [])],
            published_at=datetime.fromisoformat(
                data["published_at"].replace("Z", "+00:00")
            )
            if data.get("published_at")
            else None,
            raw_data=data,
        )


@dataclass
class VacancyCreate:
    """Data for creating a new vacancy record."""

    hh_id: str
    name: str
    employer_name: str
    employer_id: str | None = None
    area_name: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str = "RUR"
    experience: str = ""
    employment: str = ""
    schedule: str = ""
    description: str = ""
    key_skills: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_vacancy(self) -> Vacancy:
        """Convert to Vacancy entity."""
        return Vacancy(
            hh_id=self.hh_id,
            name=self.name,
            employer_name=self.employer_name,
            employer_id=self.employer_id,
            area_name=self.area_name,
            salary_from=self.salary_from,
            salary_to=self.salary_to,
            currency=self.currency,
            experience=self.experience,
            employment=self.employment,
            schedule=self.schedule,
            description=self.description,
            key_skills=self.key_skills,
            published_at=self.published_at,
            raw_data=self.raw_data,
        )
