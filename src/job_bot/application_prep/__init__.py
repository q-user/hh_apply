"""Application Preparation slice - drafts, relevance scoring, cover letters."""

from job_bot.application_prep.handlers import (
    AIError,
    ApplicationHandler,
    CoverLetterHandler,
    RelevanceHandler,
    build_filter_system_prompt_heavy,
    build_filter_system_prompt_light,
    parse_ai_json_response,
)
from job_bot.application_prep.models import (
    DEFAULT_LETTER_TEMPLATE,
    MAX_RETRIES,
    SCORE_MAX,
    SCORE_MIN,
    ApplicationDraft,
    ApplicationDraftCreate,
    CoverLetter,
    CoverLetterCreate,
    RelevanceAnalysis,
    RelevanceResult,
)
from job_bot.application_prep.ports import (
    ApplicationPort,
    CoverLetterPort,
    RelevancePort,
    RelevanceStoragePort,
)
from job_bot.application_prep.repositories import (
    ApplicationDraftRepository,
    CoverLetterRepository,
    RelevanceAnalysisRepository,
)
from job_bot.application_prep.slice import (
    ApplicationPrepSlice,
    create_application_prep_slice,
)

__all__ = [
    # Models
    "ApplicationDraft",
    "ApplicationDraftCreate",
    "CoverLetter",
    "CoverLetterCreate",
    "DEFAULT_LETTER_TEMPLATE",
    "RelevanceAnalysis",
    "RelevanceResult",
    "SCORE_MIN",
    "SCORE_MAX",
    "MAX_RETRIES",
    # Ports
    "ApplicationPort",
    "CoverLetterPort",
    "RelevancePort",
    "RelevanceStoragePort",
    # Repositories
    "ApplicationDraftRepository",
    "CoverLetterRepository",
    "RelevanceAnalysisRepository",
    # Handlers
    "ApplicationHandler",
    "CoverLetterHandler",
    "RelevanceHandler",
    "AIError",
    "build_filter_system_prompt_heavy",
    "build_filter_system_prompt_light",
    "parse_ai_json_response",
    # Slice
    "ApplicationPrepSlice",
    "create_application_prep_slice",
]
