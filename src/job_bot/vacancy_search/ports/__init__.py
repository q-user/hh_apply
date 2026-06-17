"""Ports for vacancy search slice - interfaces for cross-slice communication."""

from .search_profile_port import SearchProfilePort
from .vacancy_port import VacancyPort
from .vacancy_search_port import VacancySearchPort

__all__ = ["SearchProfilePort", "VacancyPort", "VacancySearchPort"]
