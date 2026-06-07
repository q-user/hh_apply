"""Application layer: use cases и DTO.

Этот пакет инкапсулирует бизнес-логику приложения в виде use case'ов,
отделённых от CLI/UI/HTTP-слоёв. Use case'ы принимают зависимости через
конструктор (явный DI) и не зависят от ``HHApplicantTool`` service locator.

Пакет используется:

- ``operations.apply_vacancies.Operation`` (CLI);
- ``ui.api.Api.apply_vacancies`` (UI — пока через старый путь, отвязка в #16);
- ``prepare-vacancies`` (issue #5);
- ``apply-worker`` (issue #10);
- Telegram-бот (issues #7-9).
"""

from .dto import (
    ApplyToVacanciesCommand,
    ApplyToVacanciesResult,
    PrepareVacanciesCommand,
    PrepareVacanciesResult,
)
from .use_cases import ApplyToVacanciesUseCase, PrepareVacanciesUseCase

__all__ = (
    "ApplyToVacanciesCommand",
    "ApplyToVacanciesResult",
    "ApplyToVacanciesUseCase",
    "PrepareVacanciesCommand",
    "PrepareVacanciesResult",
    "PrepareVacanciesUseCase",
)
