"""Domain models for vacancy search slice."""

from .search_profile import (
    SearchProfile,
    SearchProfileCreate,
    SearchProfileUpdate,
)
from .vacancy import Vacancy, VacancyCreate

__all__ = [
    "SearchProfile",
    "SearchProfileCreate",
    "SearchProfileUpdate",
    "Vacancy",
    "VacancyCreate",
]
