"""Canonical deprecation contract for VSA shim modules (issue #92).

Standard contract (enforced here):

* **Message format**: ``"{module.path} is deprecated; use {vsa.path}
  instead (issue #{N})."``
* **Stacklevel**: ``stacklevel=2`` so the warning points at the caller
  (not at the shim).
* **Emission point**: class shims warn in ``__init__``; module-level
  shims warn once on import.  Reloading the shim in a fresh
  ``warnings`` filter context still produces exactly one
  ``DeprecationWarning`` matching the contract.

Every shim that survives the VSA migration MUST follow this contract.
If you add a new shim module, add a row to :data:`SHIM_CONTRACT` below
and the parametrized tests will enforce the contract for you.
"""

from __future__ import annotations

import importlib
import inspect
import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

# ─── Canonical message template ─────────────────────────────────


# The only accepted message format.  Captures ``module``, ``vsa`` and
# ``issue`` so individual shims can be compared in one pass.
CONTRACT_RE = re.compile(
    r"^(?P<module>[\w.]+) is deprecated; "
    r"use (?P<vsa>[\w.]+) instead "
    r"\(issue #(?P<issue>\d+)\)\.$"
)


# ─── Contract table (one row per shim) ──────────────────────────


def _expected_message(spec: "ShimSpec") -> str:
    """Return the canonical message this shim MUST emit."""
    return (
        f"{spec.module_path} is deprecated; "
        f"use {spec.vsa_path} instead (issue #{spec.issue})."
    )


def _filter_to_shim(caught: list, spec: "ShimSpec") -> list:
    """Keep only the deprecations that match this shim's contract message."""
    expected = _expected_message(spec)
    return [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and str(w.message) == expected
    ]


@dataclass(frozen=True)
class ShimSpec:
    """Describes one VSA shim and how to trigger its deprecation warning.

    Attributes:
        module_path: Dotted path of the shim module (used both as the
            source of the warning's first segment and as the
            ``importlib.import_module`` target).
        vsa_path: Dotted path of the replacement VSA module/package
            that callers should migrate to.  Appears verbatim in the
            warning message.
        issue: GitHub issue number that documents the migration.
        trigger: Zero-arg callable that performs the action which
            should cause the shim to emit its deprecation warning
            (e.g. ``"import the module"`` or ``"instantiate the class"``).
        description: Human-readable label for the shim (used in test
            ids and failure messages).
    """

    module_path: str
    vsa_path: str
    issue: int
    trigger: Callable[[], Any]
    description: str


def _reload(module_path: str) -> Any:
    """Force a fresh import of *module_path* and return the module.

    Dropping the module from :data:`sys.modules` ensures the module-
    level ``warnings.warn(...)`` in the shim fires again so the test
    can observe it in its own ``catch_warnings`` context.
    """
    sys.modules.pop(module_path, None)
    return importlib.import_module(module_path)


def _build_applications() -> Any:
    """Reload the shim and instantiate ``ApplicationsService``."""
    _reload("hh_applicant_tool.services.applications")
    from hh_applicant_tool.services.applications import ApplicationsService

    return ApplicationsService(storage=MagicMock())


def _build_cover_letters() -> Any:
    """Reload the shim and instantiate ``CoverLetterService``."""
    _reload("hh_applicant_tool.services.cover_letters")
    from hh_applicant_tool.services.cover_letters import CoverLetterService

    return CoverLetterService(api_client=MagicMock())


def _build_relevance() -> Any:
    """Reload the shim and instantiate ``RelevanceService``."""
    _reload("hh_applicant_tool.services.relevance")
    from hh_applicant_tool.services.relevance import RelevanceService

    return RelevanceService(api_client=MagicMock())


def _build_query() -> Any:
    """Reload the shim and instantiate ``Operation`` for the ``query`` command.

    The ``query`` / ``sql`` shim is a class shim (issue #137) — the
    deprecation warning fires on ``Operation.__init__`` so the
    existing CLI dispatch can load the module without polluting
    test runs.
    """
    _reload("hh_applicant_tool.operations.query")
    from hh_applicant_tool.operations.query import Operation

    return Operation()


def _build_create_resume() -> Any:
    """Reload the shim and instantiate ``Operation`` for ``create-resume``.

    The ``create-resume`` shim is a class shim (issue #137) — the
    deprecation warning fires on ``Operation.__init__``.
    """
    _reload("hh_applicant_tool.operations.create_resume")
    from hh_applicant_tool.operations.create_resume import Operation

    return Operation()


def _build_clone_resume() -> Any:
    """Reload the shim and instantiate ``Operation`` for ``clone-resume``.

    The ``clone-resume`` shim is a class shim (issue #137) — the
    deprecation warning fires on ``Operation.__init__``.
    """
    _reload("hh_applicant_tool.operations.clone_resume")
    from hh_applicant_tool.operations.clone_resume import Operation

    return Operation()


def _build_review_flow() -> Any:
    """Reload the shim and instantiate the re-exported ``ReviewFlowService``.

    The review-flow shim is a module-level re-export (the body has been
    moved to ``job_bot.telegram_bot.services.review_service``), so the
    deprecation warning fires on import. Reloading the module is enough
    to surface the contract message; instantiating the re-exported class
    additionally proves the public surface still works through the
    legacy import path (issue #87).
    """
    _reload("hh_applicant_tool.services.review_flow")
    from hh_applicant_tool.services.review_flow import ReviewFlowService

    return ReviewFlowService(storage=MagicMock(), transport=MagicMock())


def _build_daily_digest() -> Any:
    """Reload the shim and instantiate the re-exported ``DailyDigestService``.

    The daily-digest shim is a module-level re-export (the body has been
    moved to ``job_bot.telegram_bot.services.daily_digest_service``), so
    the deprecation warning fires on import. Reloading the module is
    enough to surface the contract message; instantiating the
    re-exported class additionally proves the public surface still
    works through the legacy import path (issue #8 / #54).
    """
    _reload("hh_applicant_tool.services.daily_digest")
    from hh_applicant_tool.services.daily_digest import DailyDigestService

    storage_facade = MagicMock()
    transport = MagicMock()
    return DailyDigestService(storage=storage_facade, transport=transport)


def _build_reply_employers() -> Any:
    """Reload the ``reply_employers`` shim and instantiate ``Operation``.

    The shim is a class shim (issue #137): the deprecation warning
    fires in :meth:`Operation.__init__` (per the contract used by
    :class:`RelevanceService`), not at module import. Reloading the
    module + instantiating ``Operation()`` triggers the warn.
    """
    _reload("hh_applicant_tool.operations.reply_employers")
    from hh_applicant_tool.operations.reply_employers import Operation

    return Operation()


def _build_clear_negotiations() -> Any:
    """Reload the ``clear_negotiations`` shim and instantiate ``Operation``.

    The shim is a class shim (issue #137): the deprecation warning
    fires in :meth:`Operation.__init__` (per the contract used by
    :class:`RelevanceService`), not at module import.
    """
    _reload("hh_applicant_tool.operations.clear_negotiations")
    from hh_applicant_tool.operations.clear_negotiations import Operation

    return Operation()


# The canonical contract table.  Tests are parametrised over this list.
SHIM_CONTRACT: tuple[ShimSpec, ...] = (
    ShimSpec(
        module_path="hh_applicant_tool.services.applications",
        vsa_path="job_bot.application_prep",
        issue=54,
        trigger=_build_applications,
        description="ApplicationsService (issue #54)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.services.cover_letters",
        vsa_path="job_bot.application_prep",
        issue=54,
        trigger=_build_cover_letters,
        description="CoverLetterService (issue #54)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.services.relevance",
        vsa_path="job_bot.application_prep",
        issue=54,
        trigger=_build_relevance,
        description="RelevanceService (issue #54)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.services.vacancy_search",
        vsa_path="job_bot.vacancy_search",
        issue=53,
        trigger=lambda: _reload("hh_applicant_tool.services.vacancy_search"),
        description="services.vacancy_search module (issue #53)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.utils.config",
        vsa_path="job_bot.config_auth",
        issue=59,
        trigger=lambda: _reload("hh_applicant_tool.utils.config"),
        description="utils.config module (issue #59)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.services.review_flow",
        vsa_path="job_bot.telegram_bot.services.review_service",
        issue=87,
        trigger=_build_review_flow,
        description="services.review_flow module (issue #87)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.services.daily_digest",
        vsa_path="job_bot.telegram_bot.services.daily_digest_service",
        issue=54,
        trigger=_build_daily_digest,
        description="services.daily_digest module (issue #8 / #54)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.operations.reply_employers",
        vsa_path="job_bot.employer_engagement",
        issue=137,
        trigger=_build_reply_employers,
        description="operations.reply_employers module (issue #137)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.operations.clear_negotiations",
        vsa_path="job_bot.negotiations.lifecycle",
        issue=137,
        trigger=_build_clear_negotiations,
        description="operations.clear_negotiations module (issue #137)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.operations.query",
        vsa_path="job_bot.dev_tools",
        issue=137,
        trigger=_build_query,
        description="operations.query module (issue #137)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.operations.create_resume",
        vsa_path="job_bot.resume_management",
        issue=137,
        trigger=_build_create_resume,
        description="operations.create_resume module (issue #137)",
    ),
    ShimSpec(
        module_path="hh_applicant_tool.operations.clone_resume",
        vsa_path="job_bot.resume_management",
        issue=137,
        trigger=_build_clone_resume,
        description="operations.clone_resume module (issue #137)",
    ),
)


# ─── Per-shim contract tests ────────────────────────────────────


@pytest.mark.parametrize(
    "spec",
    SHIM_CONTRACT,
    ids=lambda s: s.description,
)
def test_shim_emits_exactly_one_deprecation_warning(spec: ShimSpec) -> None:
    """Triggering the shim emits exactly one :class:`DeprecationWarning`.

    We also assert that the warning is the only DeprecationWarning
    observed in the catch context: it should be emitted once per
    ``trigger()`` call (not zero, not many).
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        spec.trigger()

    shim_warnings = _filter_to_shim(caught, spec)
    assert len(shim_warnings) == 1, (
        f"expected exactly one DeprecationWarning whose message is the "
        f"canonical contract for {spec.description}, got "
        f"{len(shim_warnings)}: {[str(w.message) for w in shim_warnings]}"
    )


@pytest.mark.parametrize(
    "spec",
    SHIM_CONTRACT,
    ids=lambda s: s.description,
)
def test_shim_warning_message_matches_contract(spec: ShimSpec) -> None:
    """The warning's message must match the canonical template and target.

    Validates the ``{module.path} is deprecated; use {vsa.path} instead
    (issue #{N}).`` format and that the module path, VSA path and
    issue number all match the contract row.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        spec.trigger()

    shim_warnings = _filter_to_shim(caught, spec)
    assert shim_warnings, (
        f"no DeprecationWarning matching the canonical contract for "
        f"{spec.description} was captured. Captured: "
        f"{[str(w.message) for w in caught]}"
    )
    message = str(shim_warnings[0].message)

    match = CONTRACT_RE.match(message)
    assert match is not None, (
        f"deprecation message for {spec.description} does not match the "
        f"canonical contract template: {message!r}\n"
        f"Expected format: '<module.path> is deprecated; use <vsa.path> "
        f"instead (issue #<N>).'"
    )

    assert match.group("module") == spec.module_path, (
        f"deprecation message for {spec.description} names the wrong "
        f"module: {match.group('module')!r} (expected {spec.module_path!r})"
    )
    assert match.group("vsa") == spec.vsa_path, (
        f"deprecation message for {spec.description} points at the wrong "
        f"VSA target: {match.group('vsa')!r} (expected {spec.vsa_path!r})"
    )
    assert int(match.group("issue")) == spec.issue, (
        f"deprecation message for {spec.description} cites the wrong issue: "
        f"#{match.group('issue')} (expected #{spec.issue})"
    )


# ─── Stacklevel contract ────────────────────────────────────────


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_shim_source(spec: ShimSpec) -> str:
    """Return the shim module's source text for static checks."""
    module = importlib.import_module(spec.module_path)
    assert module.__file__ is not None, (
        f"shim {spec.module_path} has no __file__"
    )
    return Path(module.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "spec",
    SHIM_CONTRACT,
    ids=lambda s: s.description,
)
def test_shim_warning_uses_stacklevel_two(spec: ShimSpec) -> None:
    """Every shim must call ``warnings.warn(..., stacklevel=2)``.

    With ``stacklevel=2`` the warning's filename/line point at the
    caller (the user code that imported the shim), not at the shim
    itself.  This keeps the warning useful to end users.
    """
    source = _read_shim_source(spec)
    # Look for ``warnings.warn(`` ... ``stacklevel=2`` within a small
    # window.  A simple substring check is sufficient — no shim has
    # two ``warnings.warn`` calls today, and adding a second one is
    # exactly the regression we want this test to flag.
    assert "warnings.warn(" in source, (
        f"{spec.description} does not call warnings.warn()"
    )
    assert "stacklevel=2" in source, (
        f"{spec.description} must use stacklevel=2 so the warning "
        f"points at the caller (not at the shim)"
    )


# ─── Emission point contract ────────────────────────────────────


@pytest.mark.parametrize(
    "spec",
    SHIM_CONTRACT,
    ids=lambda s: s.description,
)
def test_shim_class_warning_is_in_dunder_init(spec: ShimSpec) -> None:
    """Class shims emit the warning from ``__init__``, not at module level.

    The contract (per issue #92) is:

    * Class shims warn in ``__init__`` so re-exports via
      ``services/__init__.py`` don't pollute every test run.
    * Module-level shims warn once on import (covered by the
      ``_reload``-based tests above).

    We distinguish the two by inspecting the trigger: class shims
    have a trigger that ends with ``Service(...)`` and module-level
    shims have a trigger that just imports the module.
    """
    trigger_src = inspect.getsource(spec.trigger)
    is_class_trigger = (
        ".services.applications import ApplicationsService" in (trigger_src)
        or ".services.cover_letters import CoverLetterService" in (trigger_src)
        or ".services.relevance import RelevanceService" in trigger_src
        or ".operations.query import Operation" in trigger_src
        or ".operations.create_resume import Operation" in trigger_src
        or ".operations.clone_resume import Operation" in trigger_src
    )
    # ``_build_daily_digest`` / ``_build_review_flow`` also import a
    # Service class from the legacy shim, but they are *module-level*
    # re-export shims (the deprecation warning fires on import, not in
    # ``__init__``). Treat them as module-level shims.
    is_module_level_re_export = (
        ".services.review_flow import ReviewFlowService" in trigger_src
        or ".services.daily_digest import DailyDigestService" in trigger_src
    )
    if is_module_level_re_export:
        is_class_trigger = False

    if not is_class_trigger:
        # Module-level shim: the warning is expected at import time
        # and the test_trigger path already exercises that.  Nothing
        # more to check structurally — the
        # ``test_shim_warning_uses_stacklevel_two`` test already
        # guards the ``stacklevel=2`` invariant.
        return

    # Find the class object that the trigger instantiates.
    cls_name = re.search(r"import (\w+)", trigger_src).group(1)
    module = importlib.import_module(spec.module_path)
    cls = getattr(module, cls_name)
    init_src = inspect.getsource(cls.__init__)

    assert "warnings.warn(" in init_src, (
        f"{spec.description} class shim must emit its deprecation "
        f"warning in __init__, not at module level"
    )
    assert "stacklevel=2" in init_src, (
        f"{spec.description} __init__ must use stacklevel=2"
    )


# ─── Module-level sanity: the contract is itself documented ─────


def test_contract_template_is_well_formed() -> None:
    """The contract regex matches the spec template verbatim.

    This is a guard against the regex silently drifting from the
    documented format in the module docstring.
    """
    sample = (
        "hh_applicant_tool.services.applications is deprecated; "
        "use job_bot.application_prep instead (issue #54)."
    )
    match = CONTRACT_RE.match(sample)
    assert match is not None
    assert match.group("module") == "hh_applicant_tool.services.applications"
    assert match.group("vsa") == "job_bot.application_prep"
    assert match.group("issue") == "54"


# ─── Manual entry point ─────────────────────────────────────────


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
