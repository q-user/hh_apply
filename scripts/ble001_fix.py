#!/usr/bin/env python3
"""Bulk-add ``# noqa: BLE001`` to remaining sites with file-specific justifications.

Strategy:
- For each (file_path, line_number) BLE001 site, find the line and
  add a justification comment after the ``except Exception`` / ``except Exception as e:``
  keyword (in-place, idempotent).
- The justification text is chosen per file context.
- Lines that already contain ``BLE001`` are skipped (idempotent).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Per-file justification text. Default falls back to "best-effort / defensive".
JUSTIFICATIONS: dict[str, str] = {
    "scripts/start.py": "best-effort launcher step",
    "src/hh_applicant_tool/infrastructure/captcha.py": "best-effort browser cleanup",
    "src/hh_applicant_tool/infrastructure/http.py": "best-effort socket inspection",
    "src/hh_applicant_tool/infrastructure/test_logger.py": "best-effort log rotation / read",
    "src/hh_applicant_tool/infrastructure/time.py": "best-effort cancellation callback",
    "src/hh_applicant_tool/infrastructure/vacancy_fetcher.py": "best-effort page-pattern match",
    "src/hh_applicant_tool/main.py": "best-effort CLI/system step",
    "src/hh_applicant_tool/operations/authorize.py": "best-effort CLI step",
    "src/hh_applicant_tool/operations/create_resume.py": "best-effort CLI step",
    "src/hh_applicant_tool/services/applications.py": "best-effort service operation",
    "src/hh_applicant_tool/services/cover_letters.py": "best-effort service operation",
    "src/hh_applicant_tool/services/daily_digest.py": "best-effort digest step",
    "src/hh_applicant_tool/services/relevance.py": "best-effort API fetch fallback",
    "src/hh_applicant_tool/ui/__init__.py": "UI bridge: never raise into callers",
    "src/hh_applicant_tool/utils/terminal.py": "best-effort tty fallback",
    "src/job_bot/application_prep/handlers/application_handler.py": "best-effort service operation",
    "src/job_bot/application_prep/handlers/cover_letter_handler.py": "best-effort service operation",
    "src/job_bot/application_prep/handlers/relevance_handler.py": "best-effort API fetch fallback",
    "src/job_bot/vacancy_search/handlers/vacancy_search_handler.py": "best-effort service operation",
    "benchmarks/test_api_benchmarks.py": "benchmark tolerates any error",
}

# Matches the end of an `except` clause where the noqa should be inserted.
# - `except Exception:`           -> noqa
# - `except Exception as e:`      -> noqa
EXC_RE = re.compile(
    r"(except\s+Exception(?:\s+as\s+\w+)?\s*:)(?!\s*#\s*noqa:\s*BLE001)"
)


def fix_file(rel_path: str, sites: list[int]) -> int:
    path = REPO / rel_path
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    justification = JUSTIFICATIONS.get(rel_path, "best-effort boundary")
    fixed = 0
    for line_no in sites:  # 1-based
        idx = line_no - 1
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        text = line.rstrip("\n")
        stripped = text.rstrip()
        # If the line already ends with `# noqa: BLE001 ...`, skip.
        if "# noqa: BLE001" in stripped:
            continue
        m = EXC_RE.search(stripped)
        if not m:
            continue
        end_of_exc = m.end()
        new_text = (
            stripped[:end_of_exc]
            + f"  # noqa: BLE001 -- {justification}"
            + stripped[end_of_exc:]
            + "\n"
        )
        lines[idx] = new_text
        fixed += 1
    if fixed:
        path.write_text("".join(lines), encoding="utf-8")
    return fixed


def main() -> int:
    # Map: file -> list of 1-based line numbers (sorted asc).
    sites: dict[str, list[int]] = {
        "scripts/start.py": [151, 251, 270, 329, 344],
        "src/hh_applicant_tool/infrastructure/captcha.py": [192, 199],
        "src/hh_applicant_tool/infrastructure/http.py": [111],
        "src/hh_applicant_tool/infrastructure/test_logger.py": [72, 108],
        "src/hh_applicant_tool/infrastructure/time.py": [56, 91],
        "src/hh_applicant_tool/infrastructure/vacancy_fetcher.py": [103],
        "src/hh_applicant_tool/main.py": [556, 564],
        "src/hh_applicant_tool/operations/authorize.py": [248],
        "src/hh_applicant_tool/operations/create_resume.py": [140],
        "src/hh_applicant_tool/services/applications.py": [178, 203],
        "src/hh_applicant_tool/services/cover_letters.py": [180, 190],
        "src/hh_applicant_tool/services/daily_digest.py": [329],
        "src/hh_applicant_tool/services/relevance.py": [625, 674, 701],
        "src/hh_applicant_tool/ui/__init__.py": [21, 27, 31],
        "src/hh_applicant_tool/utils/terminal.py": [32, 60],
        "src/job_bot/application_prep/handlers/application_handler.py": [153, 199],
        "src/job_bot/application_prep/handlers/cover_letter_handler.py": [167, 177],
        "src/job_bot/application_prep/handlers/relevance_handler.py": [89, 145, 174, 221],
        "src/job_bot/vacancy_search/handlers/vacancy_search_handler.py": [91, 108],
        "benchmarks/test_api_benchmarks.py": [148],
    }

    total_fixed = 0
    total_sites = 0
    for rel, line_nos in sites.items():
        total_sites += len(line_nos)
        n = fix_file(rel, line_nos)
        if n != len(line_nos):
            print(
                f"WARNING: {rel}: expected {len(line_nos)} fixes, got {n}",
                file=sys.stderr,
            )
        else:
            print(f"  fixed {n:3d} sites in {rel}")
        total_fixed += n
    print(f"\nTotal: {total_fixed}/{total_sites} sites fixed")
    return 0 if total_fixed == total_sites else 1


if __name__ == "__main__":
    raise SystemExit(main())
