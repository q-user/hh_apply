"""Regression tests for scripts/migrate_imports.py (issue #178).

The previous version of the migration script had two silent bugs in
its prefix-only rewrite table:

* ``ApplyToVacanciesResult`` was routed to
  ``application_submit.models.submit_result`` instead of
  ``...submit_result_dto``.
* ``from hh_applicant_tool.api.errors import CaptchaRequired`` (and
  ``LimitExceeded``) was rewritten to ``from job_bot.shared.api.errors``
  even though those two errors live in
  :mod:`job_bot.application_submit.errors` (issue #145).

These tests pin the new per-symbol rewrite table so a future
"simplification" of the table can't silently reintroduce the same
class of bug.
"""

from __future__ import annotations

import re
from pathlib import Path

# The conftest at tests/conftest.py adds scripts/ to sys.path so
# ``import migrate_imports`` works from any test in this tree.
import migrate_imports
import pytest

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "_fixtures" / "migrate_imports_fixture.py"
)


@pytest.fixture
def fixture_copy(tmp_path: Path) -> Path:
    """Copy the legacy-import fixture to a tmp path so each test gets
    a clean copy (and we never mutate the on-disk fixture).
    """
    dst = tmp_path / "fixture.py"
    dst.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


# ─── Issue #178 regression: the two specific bugs ─────────────────────


def test_apply_to_vacancies_result_goes_to_submit_result_dto(
    fixture_copy: Path,
) -> None:
    """``ApplyToVacanciesResult`` must end up in ``submit_result_dto``,
    not ``submit_result`` (regression for the original bug).
    """
    assert migrate_imports.rewrite_file(fixture_copy) is True
    text = fixture_copy.read_text(encoding="utf-8")
    assert (
        "from job_bot.application_submit.models.submit_result_dto "
        "import ApplyToVacanciesResult"
    ) in text
    assert (
        "from job_bot.application_submit.models.submit_result "
        "import ApplyToVacanciesResult"
    ) not in text


def test_captcha_and_limit_errors_go_to_application_submit(
    fixture_copy: Path,
) -> None:
    """``CaptchaRequired`` and ``LimitExceeded`` moved to
    :mod:`job_bot.application_submit.errors` (issue #145) — they
    must NOT be routed to :mod:`job_bot.shared.api.errors` along
    with the generic error classes.
    """
    migrate_imports.rewrite_file(fixture_copy)
    text = fixture_copy.read_text(encoding="utf-8")
    # Slice-specific errors go to ``application_submit.errors``.
    assert re.search(
        r"^from job_bot\.application_submit\.errors import "
        r".*\bCaptchaRequired\b",
        text,
        re.MULTILINE,
    ), "CaptchaRequired not routed to application_submit.errors"
    assert re.search(
        r"^from job_bot\.application_submit\.errors import "
        r".*\bLimitExceeded\b",
        text,
        re.MULTILINE,
    ), "LimitExceeded not routed to application_submit.errors"
    # And they must NOT be on a ``shared.api.errors`` line.
    for line in text.splitlines():
        if line.startswith("from job_bot.shared.api.errors import"):
            assert "CaptchaRequired" not in line, line
            assert "LimitExceeded" not in line, line


# ─── Full per-symbol coverage ─────────────────────────────────────────


def test_application_dto_routes_per_symbol(fixture_copy: Path) -> None:
    """``application.dto`` symbols are routed per-symbol to the right
    slice models (no fall-through to a single catch-all module).
    """
    migrate_imports.rewrite_file(fixture_copy)
    text = fixture_copy.read_text(encoding="utf-8")
    assert (
        "from job_bot.application_submit.models.command "
        "import ApplyToVacanciesCommand"
    ) in text
    assert (
        "from job_bot.application_prep.models.command "
        "import PrepareVacanciesCommand"
    ) in text
    assert (
        "from job_bot.application_prep.models.result "
        "import PrepareVacanciesResult"
    ) in text


def test_generic_api_errors_stay_in_shared(fixture_copy: Path) -> None:
    """Generic api errors (``ApiError``, ``BadResponse``, …) stay in
    :mod:`job_bot.shared.api.errors`.
    """
    migrate_imports.rewrite_file(fixture_copy)
    text = fixture_copy.read_text(encoding="utf-8")
    shared_errors_line = next(
        (
            ln
            for ln in text.splitlines()
            if ln.startswith("from job_bot.shared.api.errors import")
        ),
        None,
    )
    assert shared_errors_line is not None, (
        "expected a 'from job_bot.shared.api.errors import ...' line"
    )
    for name in (
        "ApiError",
        "BadResponse",
        "Redirect",
        "ClientError",
        "BadRequest",
        "Forbidden",
        "ResourceNotFound",
        "InternalServerError",
        "BadGateway",
    ):
        assert re.search(rf"\b{name}\b", shared_errors_line), (
            f"{name} missing from shared.api.errors line: {shared_errors_line}"
        )


def test_application_ports_go_to_shared(fixture_copy: Path) -> None:
    """``application.ports`` symbols → :mod:`job_bot.shared.ports`."""
    migrate_imports.rewrite_file(fixture_copy)
    text = fixture_copy.read_text(encoding="utf-8")
    ports_line = next(
        (
            ln
            for ln in text.splitlines()
            if ln.startswith("from job_bot.shared.ports import")
        ),
        None,
    )
    assert ports_line is not None, (
        "expected a 'from job_bot.shared.ports import ...' line"
    )
    for name in (
        "AIClientPort",
        "Clock",
        "CancellationToken",
        "CaptchaSolverPort",
        "EmailSenderPort",
        "SiteParserPort",
        "HttpClientPort",
        "DelayPort",
        "RateLimiterPort",
        "TestVacancyLoggerPort",
        "VacancyDescriptionFetcherPort",
    ):
        assert re.search(rf"\b{name}\b", ports_line), (
            f"{name} missing from shared.ports line: {ports_line}"
        )


# ─── Prefix rewrites still apply for the simpler 1-to-1 paths ─────────


def test_storage_prefix_rewrites_still_apply(fixture_copy: Path) -> None:
    """Storage prefixes (non-symbolic) still apply via PREFIX_REWRITES."""
    migrate_imports.rewrite_file(fixture_copy)
    text = fixture_copy.read_text(encoding="utf-8")
    assert (
        "from job_bot._legacy_compat.storage.facade import StorageFacade"
    ) in text


def test_constants_prefix_rewrites_still_apply(fixture_copy: Path) -> None:
    """Constants prefix still applies."""
    migrate_imports.rewrite_file(fixture_copy)
    text = fixture_copy.read_text(encoding="utf-8")
    assert ("from job_bot.shared.config.paths import HH_BASE_URL") in text


def test_main_prefix_rewrites_still_apply(fixture_copy: Path) -> None:
    """``from hh_applicant_tool.main import HHApplicantTool`` still
    routes to the legacy compat shim.
    """
    migrate_imports.rewrite_file(fixture_copy)
    text = fixture_copy.read_text(encoding="utf-8")
    assert (
        "from job_bot._legacy_compat.main_stub import HHApplicantTool"
    ) in text


def test_application_general_prefix_does_not_match_submodules(
    tmp_path: Path,
) -> None:
    """The general ``from hh_applicant_tool.application`` prefix
    must NOT match ``from hh_applicant_tool.application.dto import
    X`` (that submodule is handled by the per-symbol logic, not the
    prefix rule). The ``(?=\\s+import\\b)`` lookahead in the prefix
    rewriter is what enforces this.
    """
    p = tmp_path / "dto.py"
    p.write_text(
        "from hh_applicant_tool.application.dto import ApplyToVacanciesResult\n",
        encoding="utf-8",
    )
    migrate_imports.rewrite_file(p)
    text = p.read_text(encoding="utf-8")
    # Routed by per-symbol:
    assert (
        "from job_bot.application_submit.models.submit_result_dto "
        "import ApplyToVacanciesResult"
    ) in text
    # NOT rewritten by the general ``from hh_applicant_tool.application``
    # prefix to the wrong destination:
    assert ("from job_bot.application_prep.models.dto") not in text


def test_application_general_prefix_rewrites_real_target(
    tmp_path: Path,
) -> None:
    """The general ``from hh_applicant_tool.application import X``
    prefix DOES rewrite to ``from job_bot.application_prep.models
    import X`` for the exact-module case.
    """
    p = tmp_path / "app.py"
    p.write_text(
        "from hh_applicant_tool.application import some_helper\n",
        encoding="utf-8",
    )
    assert migrate_imports.rewrite_file(p) is True
    text = p.read_text(encoding="utf-8")
    assert ("from job_bot.application_prep.models import some_helper") in text


# ─── Unknown-symbol handling (per-symbol map miss) ────────────────────


def test_unknown_symbol_warns_and_leaves_line_untouched(
    fixture_copy: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A symbol that is not in :data:`SYMBOL_TARGETS` is left in the
    original module and a warning is emitted to stderr so the
    operator can handle it manually.
    """
    # Append an unknown-symbol import to the fixture copy.
    with fixture_copy.open("a", encoding="utf-8") as fh:
        fh.write(
            "\nfrom hh_applicant_tool.application.dto import UnknownSymbol\n"
        )

    migrate_imports.rewrite_file(fixture_copy)
    captured = capsys.readouterr()

    # The unknown symbol line is still in the file (unchanged).
    text = fixture_copy.read_text(encoding="utf-8")
    assert (
        "from hh_applicant_tool.application.dto import UnknownSymbol"
    ) in text

    # Exactly one ``import UnknownSymbol`` line — no VSA-path import
    # was silently created for the unknown symbol.
    assert text.count("import UnknownSymbol") == 1

    # A warning was emitted to stderr naming the symbol and module.
    assert "UnknownSymbol" in captured.err
    assert "hh_applicant_tool.application.dto" in captured.err


def test_unknown_symbol_mixed_with_known_symbols(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When a per-symbol block mixes known and unknown names, the
    known names are routed to their VSA targets and the unknown
    name is left in the original module.
    """
    p = tmp_path / "mixed.py"
    p.write_text(
        "from hh_applicant_tool.api.errors import "
        "ApiError, UnknownError, CaptchaRequired\n",
        encoding="utf-8",
    )
    migrate_imports.rewrite_file(p)
    captured = capsys.readouterr()
    text = p.read_text(encoding="utf-8")

    # Known names routed:
    assert "from job_bot.shared.api.errors import ApiError" in text
    assert (
        "from job_bot.application_submit.errors import CaptchaRequired" in text
    )
    # Unknown name stays in the original module:
    assert "from hh_applicant_tool.api.errors import UnknownError" in text
    # Warning emitted for the unknown name:
    assert "UnknownError" in captured.err


# ─── Idempotency ──────────────────────────────────────────────────────


def test_idempotent(fixture_copy: Path) -> None:
    """Re-running the script on an already-migrated file is a no-op."""
    assert migrate_imports.rewrite_file(fixture_copy) is True
    first_pass = fixture_copy.read_text(encoding="utf-8")
    # Second pass: no change.
    assert migrate_imports.rewrite_file(fixture_copy) is False
    second_pass = fixture_copy.read_text(encoding="utf-8")
    assert first_pass == second_pass


def test_already_migrated_file_is_noop(tmp_path: Path) -> None:
    """A file that already uses VSA-native imports is left untouched."""
    p = tmp_path / "migrated.py"
    original = (
        "from job_bot.application_submit.models.submit_result_dto "
        "import ApplyToVacanciesResult\n"
    )
    p.write_text(original, encoding="utf-8")
    assert migrate_imports.rewrite_file(p) is False
    assert p.read_text(encoding="utf-8") == original


# ─── Import-clause shapes ─────────────────────────────────────────────


def test_multiline_parenthesised_import(tmp_path: Path) -> None:
    """Multi-line parenthesised import blocks are parsed correctly."""
    p = tmp_path / "multi.py"
    p.write_text(
        "from hh_applicant_tool.application.dto import (\n"
        "    ApplyToVacanciesResult,\n"
        "    PrepareVacanciesCommand,\n"
        ")\n",
        encoding="utf-8",
    )
    assert migrate_imports.rewrite_file(p) is True
    text = p.read_text(encoding="utf-8")
    assert (
        "from job_bot.application_submit.models.submit_result_dto "
        "import ApplyToVacanciesResult"
    ) in text
    assert (
        "from job_bot.application_prep.models.command "
        "import PrepareVacanciesCommand"
    ) in text


def test_inline_single_line_import(tmp_path: Path) -> None:
    """Inline single-line imports are parsed correctly."""
    p = tmp_path / "inline.py"
    p.write_text(
        "from hh_applicant_tool.api.errors import ApiError, CaptchaRequired\n",
        encoding="utf-8",
    )
    assert migrate_imports.rewrite_file(p) is True
    text = p.read_text(encoding="utf-8")
    assert "from job_bot.shared.api.errors import ApiError" in text
    assert (
        "from job_bot.application_submit.errors import CaptchaRequired"
    ) in text


def test_single_name_import(tmp_path: Path) -> None:
    """A single-name import is rewritten to the right target module."""
    p = tmp_path / "single.py"
    p.write_text(
        "from hh_applicant_tool.application.dto import "
        "ApplyToVacanciesResult\n",
        encoding="utf-8",
    )
    assert migrate_imports.rewrite_file(p) is True
    text = p.read_text(encoding="utf-8")
    assert (
        "from job_bot.application_submit.models.submit_result_dto "
        "import ApplyToVacanciesResult"
    ) in text


# ─── Issue #190 regression: trailing comments, parenthesised non-symbolic,
#     and fixture-skip in main() ──────────────────────────────────────


def test_inline_import_with_trailing_comment(tmp_path: Path) -> None:
    """P1: An inline ``from foo import X  # comment`` line must be
    rewritten with the comment preserved on the new import line.

    Previously the greedy ``[^(\\r\\n]+`` clause swallowed the comment,
    ``_split_names`` failed to parse it, and the script emitted a
    misleading ``unknown symbol`` warning while silently leaving the
    line unchanged.
    """
    p = tmp_path / "comment_inline.py"
    p.write_text(
        "from hh_applicant_tool.api.errors import CaptchaRequired "
        "# slice-specific error\n",
        encoding="utf-8",
    )
    assert migrate_imports.rewrite_file(p) is True
    text = p.read_text(encoding="utf-8")
    assert (
        "from job_bot.application_submit.errors import CaptchaRequired"
    ) in text
    # The original legacy import must be gone (the silent no-op bug).
    assert "from hh_applicant_tool.api.errors" not in text
    # The trailing comment must be preserved on the rewritten line
    # (operator annotations belong in the diff).
    assert "# slice-specific error" in text


def test_multiline_parenthesised_import_with_inner_comment(
    tmp_path: Path,
) -> None:
    """P1: A multi-line parenthesised import with a trailing ``#``
    comment on an inner line must be rewritten cleanly.

    Previously the comment was consumed into the clause, the rewrite
    produced a ``SyntaxError``-prone line, and the comment leaked into
    the symbol stream (causing the unknown-symbol warning).
    """
    p = tmp_path / "comment_multi.py"
    p.write_text(
        "from hh_applicant_tool.application.dto import (\n"
        "    ApplyToVacanciesResult,  # the submit result\n"
        "    PrepareVacanciesCommand,\n"
        ")\n",
        encoding="utf-8",
    )
    assert migrate_imports.rewrite_file(p) is True
    text = p.read_text(encoding="utf-8")
    # Known names routed to their VSA targets:
    assert (
        "from job_bot.application_submit.models.submit_result_dto "
        "import ApplyToVacanciesResult"
    ) in text
    assert (
        "from job_bot.application_prep.models.command "
        "import PrepareVacanciesCommand"
    ) in text
    # The legacy import is gone (silent no-op bug would leave it):
    assert "from hh_applicant_tool.application.dto" not in text
    # The original inline ``# the submit result`` comment is gone (it
    # was a slice-marker, not an instruction to preserve verbatim) —
    # the operator's job is to re-annotate the new import if needed.
    assert "# the submit result" not in text


def test_parenthesised_import_from_non_symbolic_module(
    tmp_path: Path,
) -> None:
    """P2: A parenthesised import from a non-symbolic module
    (``hh_applicant_tool.ai``) must be rewritten via the prefix
    rule. Previously the ``(?=[ \\t]+import\\b)`` lookahead required
    the literal token ``import`` right after the prefix, so the
    parenthesised form was silently skipped.
    """
    p = tmp_path / "paren_non_symbolic.py"
    p.write_text(
        "from hh_applicant_tool.ai import (\n"
        "    make_client,\n"
        "    ChatMessage,\n"
        ")\n",
        encoding="utf-8",
    )
    assert migrate_imports.rewrite_file(p) is True
    text = p.read_text(encoding="utf-8")
    assert "from job_bot.shared.ai import (\n" in text
    assert "    make_client," in text
    assert "    ChatMessage," in text
    assert "from hh_applicant_tool.ai" not in text


def test_parenthesised_storage_prefix_rewrite(tmp_path: Path) -> None:
    """P2: parenthesised form for a storage module also goes through
    the prefix rule (catches the same class of bug for a different
    rewrite target).
    """
    p = tmp_path / "paren_storage.py"
    p.write_text(
        "from hh_applicant_tool.storage.facade import (\n"
        "    StorageFacade,\n"
        "    make_storage,\n"
        ")\n",
        encoding="utf-8",
    )
    assert migrate_imports.rewrite_file(p) is True
    text = p.read_text(encoding="utf-8")
    assert "from job_bot._legacy_compat.storage.facade import (" in text
    assert "    StorageFacade," in text
    assert "    make_storage," in text
    assert "from hh_applicant_tool.storage" not in text


def test_main_skips_tests_fixtures_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P3: ``main()`` must NOT walk into ``tests/_fixtures/``.

    The fixture file under ``tests/_fixtures/`` is a static data
    file that mirrors the legacy-import shape on purpose — running
    ``main()`` against the real tree would migrate it in place and
    break every other test in this module.
    """
    # Build a tiny self-contained tree in tmp_path that mirrors the
    # production layout: a ``src/job_bot`` package, a ``tests``
    # package, and a ``tests/_fixtures`` package containing a
    # legacy-import file. We then ``chdir`` into the tmp tree and
    # invoke ``main()`` and assert (a) the source file in
    # ``src/job_bot`` IS rewritten, and (b) the fixture file under
    # ``tests/_fixtures/`` is left untouched.
    (tmp_path / "src" / "job_bot").mkdir(parents=True)
    src_file = tmp_path / "src" / "job_bot" / "app.py"
    src_file.write_text(
        "from hh_applicant_tool.constants import HH_BASE_URL\n",
        encoding="utf-8",
    )

    (tmp_path / "tests" / "_fixtures").mkdir(parents=True)
    fixture_file = tmp_path / "tests" / "_fixtures" / "fixture.py"
    original_fixture_text = (
        "from hh_applicant_tool.constants import HH_BASE_URL\n"
    )
    fixture_file.write_text(original_fixture_text, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    rc = migrate_imports.main()
    assert rc == 0
    captured = capsys.readouterr()
    assert "Rewrote imports in 1 files" in captured.out, captured.out

    # The real source file under src/job_bot was rewritten.
    assert (
        "from job_bot.shared.config.paths import HH_BASE_URL"
    ) in src_file.read_text(encoding="utf-8")

    # The fixture file was NOT touched (it would be if the
    # ``_fixtures`` skip was missing).
    assert fixture_file.read_text(encoding="utf-8") == original_fixture_text
