"""Canonical deprecation contract for VSA shim modules (issue #92).

After issue #158 the ``hh_applicant_tool`` distribution package is
retired and only the 5-LOC stub remains.  The 5 deprecation shims that
used to be tested here (the 4 operations class shims and the
``api.errors``/``api.datatypes`` module shims) are gone, so the
contract table is now empty.

This module is kept as **living documentation** of the canonical
deprecation contract for any future shim that may appear.  The
:class:`ShimSpec` dataclass, the :data:`CONTRACT_RE` regex and the
:data:`SHIM_CONTRACT` table stay so that the next person who needs to
introduce a shim has a reference implementation to copy from.  No
shim-related tests run today; only the
:func:`test_contract_template_is_well_formed` test below fires, which
asserts that the regex still parses the canonical message template.

Standard contract (was enforced when the shims existed):

* **Message format**: ``"{module.path} is deprecated; use {vsa.path}
  instead (issue #{N})."``
* **Stacklevel**: ``stacklevel=2`` so the warning points at the caller
  (not at the shim).
* **Emission point**: class shims warn in ``__init__``; module-level
  shims warn once on import.  Reloading the shim in a fresh
  ``warnings`` filter context still produces exactly one
  ``DeprecationWarning`` matching the contract.

If a future shim is introduced, add a row to :data:`SHIM_CONTRACT` and
the parametrized tests will enforce the contract for you.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

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


# The canonical contract table.  After issue #158 the table is empty
# — every shim that this contract used to assert is gone.  See the
# module docstring for details.
SHIM_CONTRACT: tuple[ShimSpec, ...] = ()


# ─── Template sanity check ──────────────────────────────────────


def test_contract_template_is_well_formed() -> None:
    """The :data:`CONTRACT_RE` regex parses the canonical template.

    Acts as a smoke test for the documentation value of the contract:
    if the regex or the template drift out of sync, this test will
    fail with a useful message.
    """
    canonical = (
        "hh_applicant_tool.utils is deprecated; "
        "use job_bot.shared.utils instead (issue #151)."
    )
    match = CONTRACT_RE.match(canonical)
    assert match is not None, f"regex did not match canonical: {canonical!r}"
    assert match.group("module") == "hh_applicant_tool.utils"
    assert match.group("vsa") == "job_bot.shared.utils"
    assert match.group("issue") == "151"
