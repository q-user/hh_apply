"""Fixture for tests/test_migrate_imports.py.

Contains legacy ``hh_applicant_tool.*`` imports that
``scripts/migrate_imports.py`` should rewrite to their VSA-native
targets. The exact form of the imports (multi-line parenthesised vs
inline single-line) mirrors what the real codebase has, so the
fixture exercises both the per-symbol and the prefix rewrite paths.

This module is *data*, not executable code: the test reads the
text and asserts on the rewritten output, never ``import``s the
fixture. Every name is therefore unused from a static-analysis
perspective, hence the blanket ``# noqa: F401`` on each import.
"""

# ruff: noqa: F401
from hh_applicant_tool.api.errors import (
    ApiError,
    BadGateway,
    BadRequest,
    BadResponse,
    CaptchaRequired,
    ClientError,
    Forbidden,
    InternalServerError,
    LimitExceeded,
    Redirect,
    ResourceNotFound,
)
from hh_applicant_tool.application.dto import (
    ApplyToVacanciesCommand,
    ApplyToVacanciesResult,
    PrepareVacanciesCommand,
    PrepareVacanciesResult,
)
from hh_applicant_tool.application.ports import (
    AIClientPort,
    CancellationToken,
    CaptchaSolverPort,
    Clock,
    DelayPort,
    EmailSenderPort,
    HttpClientPort,
    RateLimiterPort,
    SiteParserPort,
    TestVacancyLoggerPort,
    VacancyDescriptionFetcherPort,
)
from hh_applicant_tool.constants import HH_BASE_URL
from hh_applicant_tool.main import HHApplicantTool
from hh_applicant_tool.storage.facade import StorageFacade
