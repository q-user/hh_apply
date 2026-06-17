"""VSA-native CLI package (issue #147).

This package is the new canonical home for all CLI sub-commands. The
:class:`BUILTIN_OPERATIONS` tuple is the immutable registry the next PR
(issue #148) will use to replace the ``pkgutil.iter_modules`` walker in
``hh_applicant_tool.main._create_parser``.

Each sub-command is a self-contained class that takes its dependencies
(the VSA slice it needs) via constructor injection — no more
``tool: HHApplicantTool`` argument. The new ``BaseOperation`` /
``BaseNamespace`` base classes (see :mod:`._base`) replace the legacy
``hh_applicant_tool.main.BaseOperation`` / ``BaseNamespace``.
"""

from __future__ import annotations

from ._base import BaseNamespace, BaseOperation
from .apply_vacancies import Operation as ApplyVacanciesOperation
from .apply_worker import Operation as ApplyWorkerOperation
from .call_api import Operation as CallApiOperation
from .channel_monitor import Operation as ChannelMonitorOperation
from .check_proxy import Operation as CheckProxyOperation
from .clear_skipped import Operation as ClearSkippedOperation
from .config import Operation as ConfigOperation
from .install import Operation as InstallOperation
from .list_resumes import Operation as ListResumesOperation
from .log import Operation as LogOperation
from .logout import Operation as LogoutOperation
from .max_bot import Operation as MaxBotOperation
from .migrate_db import Operation as MigrateDbOperation
from .prepare_vacancies import Operation as PrepareVacanciesOperation
from .refresh_token import Operation as RefreshTokenOperation
from .settings import Operation as SettingsOperation
from .telegram_bot import Operation as TelegramBotOperation
from .test_session import Operation as TestSessionOperation
from .uninstall import Operation as UninstallOperation
from .update_resumes import Operation as UpdateResumesOperation
from .whoami import Operation as WhoamiOperation

# ─── Registry ────────────────────────────────────────────────────────
#
# The canonical list of all built-in CLI operations. Adding a new
# sub-command is a 3-line change:
#   1. create ``job_bot/cli/<name>.py`` with ``class Operation(BaseOperation)``,
#   2. import the class here,
#   3. add it to ``BUILTIN_OPERATIONS``.
#
# Issue #148 will switch ``main._create_parser`` to iterate this tuple
# instead of walking ``hh_applicant_tool/operations/`` with
# ``pkgutil.iter_modules``.

BUILTIN_OPERATIONS: tuple[type[BaseOperation], ...] = (
    # ── 13 new sub-commands (issue #147) ──
    CallApiOperation,
    CheckProxyOperation,
    ClearSkippedOperation,
    ConfigOperation,
    InstallOperation,
    ListResumesOperation,
    LogOperation,
    LogoutOperation,
    MigrateDbOperation,
    RefreshTokenOperation,
    SettingsOperation,
    TestSessionOperation,
    UninstallOperation,
    UpdateResumesOperation,
    WhoamiOperation,
    # ── 6 existing VSA ops (re-typed in issue #147) ──
    ApplyVacanciesOperation,
    ApplyWorkerOperation,
    ChannelMonitorOperation,
    MaxBotOperation,
    PrepareVacanciesOperation,
    TelegramBotOperation,
)

__all__ = [
    "BUILTIN_OPERATIONS",
    "BaseNamespace",
    "BaseOperation",
]
