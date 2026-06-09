"""Сервисный слой для подготовки и отправки откликов.

Содержит переиспользуемые компоненты, вынесенные из монолитного
``operations/apply_vacancies.py``. Сервисы принимают зависимости через
конструктор (``api_client``, ``ai_client``, ``storage``), что упрощает
юнит-тестирование и переиспользование в других операциях
(``prepare-vacancies`` — issue #5, ``apply-worker`` — issue #10).
"""

from __future__ import annotations

from .applications import ApplicationsService
from .apply_one import make_default_apply_one
from .apply_worker import (
    DEFAULT_MAX_ATTEMPTS,
    ApplyOneDraftFn,
    ApplyWorkerService,
    FatalError,
    ProcessResult,
    RetryableError,
    RunStats,
)
from .cover_letters import DEFAULT_LETTER_TEMPLATE, CoverLetterService
from .daily_digest import (
    LAST_DIGEST_KEY,
    DailyDigestService,
    DigestResult,
    DraftGroup,
)
from .relevance import (
    RelevanceResult,
    RelevanceService,
    build_filter_system_prompt_heavy,
    build_filter_system_prompt_light,
    parse_ai_json_response,
)
from .review_flow import ReviewFlowService
from .vacancy_search import VacancySearchService, build_search_params
from .vacancy_tests import VacancyTestsService

__all__ = (
    "ApplicationsService",
    "ApplyOneDraftFn",
    "ApplyWorkerService",
    "CoverLetterService",
    "DEFAULT_LETTER_TEMPLATE",
    "DEFAULT_MAX_ATTEMPTS",
    "DailyDigestService",
    "DigestResult",
    "DraftGroup",
    "FatalError",
    "LAST_DIGEST_KEY",
    "ProcessResult",
    "RelevanceResult",
    "RelevanceService",
    "RetryableError",
    "ReviewFlowService",
    "RunStats",
    "VacancySearchService",
    "VacancyTestsService",
    "build_filter_system_prompt_heavy",
    "build_filter_system_prompt_light",
    "build_search_params",
    "make_default_apply_one",
    "parse_ai_json_response",
)
