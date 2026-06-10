"""Vacancy Search slice - search profiles, collection, HH API client."""

from .models import (
    SearchProfile,
    SearchProfileCreate,
    SearchProfileUpdate,
    Vacancy,
    VacancyCreate,
)
from .ports import SearchProfilePort, VacancyPort, VacancySearchPort
from .slice import VacancySearchSlice, create_vacancy_search_slice

__all__ = [
    # Models
    "SearchProfile",
    "SearchProfileCreate",
    "SearchProfileUpdate",
    "Vacancy",
    "VacancyCreate",
    # Ports
    "SearchProfilePort",
    "VacancyPort",
    "VacancySearchPort",
    # Slice
    "VacancySearchSlice",
    "create_vacancy_search_slice",
]
