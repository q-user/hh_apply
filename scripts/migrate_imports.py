#!/usr/bin/env python3
"""Bulk-update VSA imports from `hh_applicant_tool.*` to VSA paths.

Issue #158 — delete the legacy `hh_applicant_tool/` distribution package.

This script rewrites imports inside ``src/job_bot/`` and ``tests/`` to
point at the VSA-native locations. It is idempotent (safe to re-run).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Map of "old import prefix" -> "new import prefix" (most-specific first).
# Order matters: longer / more-specific prefixes must be tried before
# shorter ones (e.g. ``hh_applicant_tool.storage.models`` before
# ``hh_applicant_tool.storage``).
REWRITES: tuple[tuple[str, str], ...] = (
    # ─── AI ───────────────────────────────────────────────────────
    (
        "from hh_applicant_tool.ai.openai",
        "from job_bot.shared.ai._chat_openai",
    ),
    (
        "from hh_applicant_tool.ai.base",
        "from job_bot.shared.ai._errors",
    ),
    (
        "from hh_applicant_tool.ai",
        "from job_bot.shared.ai",
    ),
    # ─── Application layer ──────────────────────────────────────
    # DTOs → per-slice models; Protocols → per-slice ports.
    (
        "from hh_applicant_tool.application.dto import ApplyToVacanciesCommand",
        "from job_bot.application_submit.models.command import ApplyToVacanciesCommand",
    ),
    (
        "from hh_applicant_tool.application.dto import ApplyToVacanciesResult",
        "from job_bot.application_submit.models.submit_result import ApplyToVacanciesResult",
    ),
    (
        "from hh_applicant_tool.application.dto import PrepareVacanciesCommand",
        "from job_bot.application_prep.models.command import PrepareVacanciesCommand",
    ),
    (
        "from hh_applicant_tool.application.dto import PrepareVacanciesResult",
        "from job_bot.application_prep.models.result import PrepareVacanciesResult",
    ),
    (
        "from hh_applicant_tool.application.dto import",
        "from job_bot.application_submit.models.command import",
    ),
    (
        "from hh_applicant_tool.application.ports import",
        "from job_bot.application_submit.ports.port_defs import",
    ),
    (
        "from hh_applicant_tool.application.use_cases",
        "from job_bot.application_submit.services.use_cases",
    ),
    (
        "from hh_applicant_tool.application",
        "from job_bot.application_prep.models",
    ),
    # ─── Storage (legacy compat) ─────────────────────────────────
    (
        "from hh_applicant_tool.storage.models",
        "from job_bot._legacy_compat.storage.models",
    ),
    (
        "from hh_applicant_tool.storage.repositories",
        "from job_bot._legacy_compat.storage.repositories",
    ),
    (
        "from hh_applicant_tool.storage.facade import StorageFacade",
        "from job_bot._legacy_compat.storage.facade import StorageFacade",
    ),
    (
        "from hh_applicant_tool.storage.facade",
        "from job_bot._legacy_compat.storage.facade",
    ),
    (
        "from hh_applicant_tool.storage.utils",
        "from job_bot._legacy_compat.storage.utils",
    ),
    (
        "from hh_applicant_tool.storage",
        "from job_bot._legacy_compat.storage",
    ),
    # ─── API errors / datatypes ──────────────────────────────────
    (
        "from hh_applicant_tool.api.errors",
        "from job_bot.shared.api.errors",
    ),
    (
        "from hh_applicant_tool.api.datatypes",
        "from job_bot.shared.api.datatypes",
    ),
    (
        "from hh_applicant_tool.api",
        "from job_bot.shared.api",
    ),
    # ─── Utils (already a shim; jobs in shared.utils) ────────────
    (
        "from hh_applicant_tool.utils",
        "from job_bot.shared.utils",
    ),
    # ─── Constants ───────────────────────────────────────────────
    (
        "from hh_applicant_tool.constants",
        "from job_bot.shared.config.paths",
    ),
    # ─── Container ───────────────────────────────────────────────
    (
        "from hh_applicant_tool.container import AppContainer",
        "from job_bot.container import AppContainer",
    ),
    (
        "from hh_applicant_tool.container",
        "from job_bot.container",
    ),
    # ─── Main (HHApplicantTool service locator) ──────────────────
    # After issue #158, no shim survives. Callers that needed it
    # are rewritten to use ``job_bot.container.AppContainer``.
    (
        "from hh_applicant_tool.main import HHApplicantTool",
        "from job_bot._legacy_compat.main_stub import HHApplicantTool",
    ),
    (
        "from hh_applicant_tool.main",
        "from job_bot._legacy_compat.main_stub",
    ),
)


def rewrite_file(path: Path) -> bool:
    """Rewrite imports in *path*; return True if the file changed."""
    text = path.read_text(encoding="utf-8")
    new = text
    for old, replacement in REWRITES:
        new = new.replace(old, replacement)
    if new != text:
        path.write_text(new, encoding="utf-8")
        return True
    return False


def main() -> int:
    root = Path("src/job_bot")
    test_root = Path("tests")
    changed = 0
    for base in (root, test_root):
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            if rewrite_file(p):
                changed += 1
    print(f"Rewrote imports in {changed} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
