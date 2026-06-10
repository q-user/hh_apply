"""Application Preparation models."""

from job_bot.application_prep.models.application import (
    ApplicationDraft,
    ApplicationDraftCreate,
)
from job_bot.application_prep.models.cover_letter import (
    DEFAULT_LETTER_TEMPLATE,
    CoverLetter,
    CoverLetterCreate,
)
from job_bot.application_prep.models.relevance import (
    MAX_RETRIES,
    SCORE_MAX,
    SCORE_MIN,
    RelevanceAnalysis,
    RelevanceResult,
)

__all__ = [
    "CoverLetter",
    "CoverLetterCreate",
    "DEFAULT_LETTER_TEMPLATE",
    "RelevanceResult",
    "RelevanceAnalysis",
    "SCORE_MIN",
    "SCORE_MAX",
    "MAX_RETRIES",
    "ApplicationDraft",
    "ApplicationDraftCreate",
]
