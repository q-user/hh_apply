"""Сервисный слой для подготовки и отправки откликов.

Содержит переиспользуемые компоненты, вынесенные из монолитного
``operations/apply_vacancies.py``. Сервисы принимают зависимости через
конструктор (``api_client``, ``ai_client``, ``storage``), что упрощает
юнит-тестирование и переиспользование в других операциях
(``prepare-vacancies`` — issue #5, ``apply-worker`` — issue #4).
"""

from __future__ import annotations

from .applications import ApplicationsService
from .cover_letters import CoverLetterService
from .relevance import (
    RelevanceResult,
    RelevanceService,
    build_filter_system_prompt_heavy,
    build_filter_system_prompt_light,
    parse_ai_json_response,
)
from .vacancy_search import VacancySearchService, build_search_params
from .vacancy_tests import VacancyTestsService

__all__ = (
    "ApplicationsService",
    "CoverLetterService",
    "RelevanceResult",
    "RelevanceService",
    "VacancySearchService",
    "VacancyTestsService",
    "build_filter_system_prompt_heavy",
    "build_filter_system_prompt_light",
    "build_search_params",
    "parse_ai_json_response",
)
