"""Use case'ы application layer.

Каждый use case инкапсулирует один бизнес-сценарий и принимает
зависимости через конструктор (явный DI).
"""

from .apply_to_vacancies import ApplyToVacanciesUseCase

__all__ = ("ApplyToVacanciesUseCase",)
