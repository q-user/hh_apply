#!/usr/bin/env python3
"""Bulk-update VSA imports from `hh_applicant_tool.*` to VSA paths.

Issue #158 — delete the legacy ``hh_applicant_tool/`` distribution package.
Issue #178 — switch to a per-symbol rewrite table so future re-runs
do not silently reintroduce the wrong targets from the previous
prefix-only design. The old ``REWRITES`` table had two bugs:

1. ``ApplyToVacanciesResult`` was routed to
   ``application_submit.models.submit_result`` instead of
   ``...submit_result_dto`` (where it actually lives — see
   ``src/job_bot/application_submit/models/submit_result_dto.py``).
2. ``from hh_applicant_tool.api.errors import …`` was rewritten
   wholesale to ``from job_bot.shared.api.errors``, but
   :class:`CaptchaRequired` and :class:`LimitExceeded` moved to
   :mod:`job_bot.application_submit.errors` per issue #145.

The fix: for source modules that have been split across multiple
VSA modules (``application.dto``, ``application.ports``,
``api.errors``) the import clause is parsed name-by-name and each
name is routed via :data:`SYMBOL_TARGETS`. Names not in the map are
left in the original module and a warning is emitted to stderr so
the operator can handle them manually. The simpler 1-to-1 module
paths continue to use prefix-based rewrites (:data:`PREFIX_REWRITES`).

Issue #190 — three latent regex bugs and one CI hazard in the
per-symbol / prefix rewrites:

1. The inline ``clause`` alternation was greedy and swallowed
   trailing ``#`` comments, so ``from foo import X  # comment``
   was silently left unchanged with a misleading
   ``unknown symbol`` warning.
2. Multi-line parenthesised imports with a trailing comment on an
   inner line produced ``SyntaxError``-prone output. The inner
   comment is now stripped before name parsing.
3. The prefix-rewrite lookahead required the literal ``import``
   keyword, which silently skipped parenthesised imports from
   non-symbolic modules. The lookahead now also matches ``(``.
4. ``main()`` walked into ``tests/_fixtures/`` and would migrate
   the static fixture file in place if ever run as part of CI.
   That directory is now excluded.

The script is idempotent — re-running it on already-migrated code
is a no-op (the regex never matches a ``job_bot.*`` import).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ─── Per-symbol rewrite table (issue #178) ────────────────────────────
# Key: symbol *name* imported from a legacy module. Value: the VSA
# module the name should be imported from. This is the only safe way
# to handle the application-submit ↔ application-prep split and the
# ``api.errors`` ↔ ``application_submit.errors`` split (issue #145):
# a single prefix rewrite would either miss names that moved to a
# different VSA module or, worse, silently route them to the wrong
# one (which is exactly the bug the previous table had).
SYMBOL_TARGETS: dict[str, str] = {
    # application.dto → per-slice models
    "ApplyToVacanciesCommand": "job_bot.application_submit.models.command",
    "ApplyToVacanciesResult": "job_bot.application_submit.models.submit_result_dto",
    "PrepareVacanciesCommand": "job_bot.application_prep.models.command",
    "PrepareVacanciesResult": "job_bot.application_prep.models.result",
    # application.ports → shared kernel
    "AIClientPort": "job_bot.shared.ports",
    "Clock": "job_bot.shared.ports",
    "CancellationToken": "job_bot.shared.ports",
    "CaptchaSolverPort": "job_bot.shared.ports",
    "EmailSenderPort": "job_bot.shared.ports",
    "SiteParserPort": "job_bot.shared.ports",
    "HttpClientPort": "job_bot.shared.ports",
    "DelayPort": "job_bot.shared.ports",
    "RateLimiterPort": "job_bot.shared.ports",
    "TestVacancyLoggerPort": "job_bot.shared.ports",
    "VacancyDescriptionFetcherPort": "job_bot.shared.ports",
    # api.errors → slice-specific errors live with the slice (issue #145)
    "CaptchaRequired": "job_bot.application_submit.errors",
    "LimitExceeded": "job_bot.application_submit.errors",
    # generic api errors stay in the shared kernel
    "ApiError": "job_bot.shared.api.errors",
    "BadResponse": "job_bot.shared.api.errors",
    "Redirect": "job_bot.shared.api.errors",
    "ClientError": "job_bot.shared.api.errors",
    "BadRequest": "job_bot.shared.api.errors",
    "Forbidden": "job_bot.shared.api.errors",
    "ResourceNotFound": "job_bot.shared.api.errors",
    "InternalServerError": "job_bot.shared.api.errors",
    "BadGateway": "job_bot.shared.api.errors",
}

# Source modules whose imports are rewritten per-symbol (parsed
# name-by-name via :data:`SYMBOL_TARGETS`). When :data:`_FROM_IMPORT_RE`
# matches a line whose module is in this set, the import clause is
# processed symbolically rather than via the prefix-rewrite table.
SYMBOLIC_SOURCE_MODULES: frozenset[str] = frozenset(
    {
        "hh_applicant_tool.application.dto",
        "hh_applicant_tool.application.ports",
        "hh_applicant_tool.api.errors",
    }
)


# ─── Prefix rewrites for the simpler 1-to-1 module paths ─────────────
# Most-specific first. The actual matching is done with a regex that
# requires the prefix to be followed by whitespace + the ``import``
# keyword (see :func:`_apply_prefix_rewrites`), so an entry like
# ``from hh_applicant_tool.application`` does *not* accidentally
# match ``from hh_applicant_tool.application.dto import X`` (the dot
# after ``application`` is not whitespace).
PREFIX_REWRITES: tuple[tuple[str, str], ...] = (
    # ─── AI ───────────────────────────────────────────────────────
    ("from hh_applicant_tool.ai.openai", "from job_bot.shared.ai._chat_openai"),
    ("from hh_applicant_tool.ai.base", "from job_bot.shared.ai._errors"),
    ("from hh_applicant_tool.ai", "from job_bot.shared.ai"),
    # ─── Application layer (no per-symbol target) ────────────────
    (
        "from hh_applicant_tool.application.use_cases",
        "from job_bot.application_submit.services.use_cases",
    ),
    # The general ``from hh_applicant_tool.application`` rewrite must
    # NOT match ``.dto`` / ``.ports`` submodules — those are handled
    # by the per-symbol pass above. The regex lookahead in
    # ``_apply_prefix_rewrites`` enforces this.
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
        "from hh_applicant_tool.storage.facade",
        "from job_bot._legacy_compat.storage.facade",
    ),
    (
        "from hh_applicant_tool.storage.utils",
        "from job_bot._legacy_compat.storage.utils",
    ),
    ("from hh_applicant_tool.storage", "from job_bot._legacy_compat.storage"),
    # ─── API datatypes (other than errors handled symbolically) ──
    (
        "from hh_applicant_tool.api.datatypes",
        "from job_bot.shared.api.datatypes",
    ),
    ("from hh_applicant_tool.api", "from job_bot.shared.api"),
    # ─── Utils (already a shim; jobs in shared.utils) ────────────
    ("from hh_applicant_tool.utils", "from job_bot.shared.utils"),
    # ─── Constants ───────────────────────────────────────────────
    ("from hh_applicant_tool.constants", "from job_bot.shared.config.paths"),
    # ─── Container ───────────────────────────────────────────────
    ("from hh_applicant_tool.container", "from job_bot.container"),
    # ─── Main (HHApplicantTool service locator) ──────────────────
    # After issue #158, no shim survives. Callers that needed it
    # are rewritten to use ``job_bot.container.AppContainer``.
    ("from hh_applicant_tool.main", "from job_bot._legacy_compat.main_stub"),
)


# Match a single ``from <module> import <clause>`` statement. The
# clause is either a parenthesised list (possibly multi-line) or an
# inline single-line list. An optional trailing comment on the same
# line as the closing token is preserved in group ``trailer``.
# Issue #190: the inline ``clause`` alternation now also stops at
# ``#`` so a trailing inline comment is captured in the ``trailer``
# group (preserved on the rewritten line) rather than swallowed into
# the clause. Comments inside the parenthesised form are stripped in
# :func:`_replace_symbolic` before name parsing.
_FROM_IMPORT_RE = re.compile(
    r"^([ \t]*)from[ \t]+"
    r"(?P<module>[A-Za-z_][\w.]*)"
    r"[ \t]+import[ \t]+"
    r"(?P<clause>\((?:[^)]*)\)|[^(\r\n#]+)"
    r"(?P<trailer>[ \t]*\#[^\n]*)?",
    re.MULTILINE,
)

# Match a single name in an import clause, optionally followed by
# `` as <alias>``.
_NAME_RE = re.compile(
    r"([A-Za-z_][\w]*)"
    r"(?:\s+as\s+([A-Za-z_][\w]*))?"
)


def _strip_inline_comments(text: str) -> str:
    """Strip trailing ``# ...`` comments from each line of *text*.

    Issue #190: a multi-line parenthesised import can have a trailing
    ``#`` comment on an inner line (e.g. ``A,  # submit result``). The
    per-symbol regex captures the whole parenthesised block greedily,
    so the comment would otherwise leak into the symbol stream and
    produce a misleading ``unknown symbol`` warning. We strip it
    here before name parsing.

    Note: import clauses don't contain string literals, so a naive
    ``line.find("#")`` is safe.
    """
    lines = text.split("\n")
    stripped: list[str] = []
    for line in lines:
        idx = line.find("#")
        if idx >= 0:
            line = line[:idx]
        stripped.append(line.rstrip())
    return "\n".join(stripped)


def _split_names(clause: str) -> list[tuple[str, str | None]]:
    """Parse an import clause into ``(name, alias)`` pairs.

    Handles both inline ``A, B, C`` and parenthesised ``(A, B, C)``
    forms (including trailing commas and arbitrary whitespace). A
    bare ``*`` star import is returned as ``("*", None)`` so the
    caller can warn and leave it alone.
    """
    text = clause.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    text = text.strip()
    if not text:
        return []
    if text.endswith(","):
        text = text[:-1]
    text = text.strip()
    result: list[tuple[str, str | None]] = []
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if piece == "*":
            result.append(("*", None))
            continue
        m = _NAME_RE.fullmatch(piece)
        if not m:
            # Unparseable token — preserve verbatim so the caller can
            # warn without losing information.
            result.append((piece, None))
            continue
        result.append((m.group(1), m.group(2)))
    return result


def _format_name(name: str, alias: str | None) -> str:
    if alias and alias != name:
        return f"{name} as {alias}"
    return name


def _rewrite_symbolic_import(
    module: str,
    clause: str,
    file_path: Path,
) -> list[str] | None:
    """Rewrite a per-symbol import. Returns a list of new import lines.

    Returns ``None`` if the rewrite is a no-op (e.g. star import with
    no other names).
    """
    pairs = _split_names(clause)
    if not pairs:
        return None

    has_star = any(name == "*" for name, _ in pairs)
    if has_star:
        print(
            f"warning: star import in 'from {module} import ...' "
            f"in {file_path}; cannot auto-rewrite the star part",
            file=sys.stderr,
        )

    groups: dict[str, list[tuple[str, str | None]]] = {}
    for name, alias in pairs:
        if name == "*":
            # Keep the star in the original module — it can't be
            # routed per-symbol.
            groups.setdefault(module, []).append((name, alias))
            continue
        target = SYMBOL_TARGETS.get(name)
        if target is None:
            print(
                f"warning: unknown symbol {name!r} (imported from {module})"
                f" in {file_path}; left unchanged — handle manually",
                file=sys.stderr,
            )
            groups.setdefault(module, []).append((name, alias))
        else:
            groups.setdefault(target, []).append((name, alias))

    return [
        f"from {target} import {', '.join(_format_name(n, a) for n, a in items)}"
        for target, items in groups.items()
    ]


def _replace_symbolic(match: re.Match[str], file_path: Path) -> str:
    module = match.group("module")
    if module not in SYMBOLIC_SOURCE_MODULES:
        return match.group(0)
    clause = match.group("clause")
    # Issue #190: strip inline comments from the parenthesised form
    # so they don't leak into the symbol stream. (The inline form's
    # comment, if any, is already separated into ``trailer`` by the
    # tightened regex.)
    if clause.startswith("("):
        clause = _strip_inline_comments(clause)
    replacement = _rewrite_symbolic_import(module, clause, file_path)
    if replacement is None:
        return match.group(0)
    indent = match.group(1)
    trailer = match.group("trailer") or ""
    return "\n".join(indent + line for line in replacement) + trailer


def _apply_prefix_rewrites(text: str) -> str:
    """Apply the simple prefix rewrites (non-symbolic modules).

    The regex anchor ``(?=[ \\t]+import\\b|[ \\t]*\\()`` requires
    the prefix to be followed by either whitespace + the ``import``
    keyword (inline form) or whitespace + an open paren (parenthesised
    form). This means a pattern like ``from hh_applicant_tool.application``
    does *not* match ``from hh_applicant_tool.application.dto import X``
    (the dot is not whitespace) AND the parenthesised form
    ``from hh_applicant_tool.ai import (\\n    A,\\n)`` is correctly
    rewritten (issue #190: previously the lookahead required the
    literal ``import`` token, which silently skipped the
    parenthesised form).
    """
    for old, new in PREFIX_REWRITES:
        pattern = re.compile(
            rf"^({re.escape(old)})(?=[ \t]+import\b|[ \t]*\()",
            re.MULTILINE,
        )
        text = pattern.sub(new, text)
    return text


def rewrite_file(path: Path) -> bool:
    """Rewrite imports in *path*; return True if the file changed."""
    text = path.read_text(encoding="utf-8")
    new = text

    # Per-symbol pass first (issue #178) — must run before the
    # generic prefix rewrites so that ``application.dto`` /
    # ``application.ports`` / ``api.errors`` are routed to the right
    # per-slice targets rather than being silently caught by the
    # more-general prefix rules.
    new = _FROM_IMPORT_RE.sub(lambda m: _replace_symbolic(m, path), new)

    # Prefix rewrites for the remaining 1-to-1 module paths.
    new = _apply_prefix_rewrites(new)

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
            # Issue #190: skip the static fixture directory. The
            # fixture under ``tests/_fixtures/`` mirrors the
            # legacy-import shape on purpose; migrating it in place
            # would break every other test in this module.
            if "_fixtures" in p.parts:
                continue
            if rewrite_file(p):
                changed += 1
    print(f"Rewrote imports in {changed} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
