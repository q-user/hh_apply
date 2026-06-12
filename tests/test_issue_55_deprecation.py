"""Tests for issue #55: deprecation warnings on legacy apply services.

The legacy ``hh_applicant_tool.services.apply_one`` and
``hh_applicant_tool.services.apply_worker`` modules are being replaced
by the VSA ``ApplicationSubmitSlice``. The legacy modules are kept
for backward compatibility but must emit ``DeprecationWarning`` on
import so downstream code can find the deprecation via Python's
default warning machinery.
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest


def test_apply_one_module_emits_deprecation_warning() -> None:
    """``hh_applicant_tool.services.apply_one`` must warn on import.

    Forced reload ensures the test catches the warning even if the
    module was already imported by an earlier test in the same
    ``pytest`` run (the import is guarded by ``stacklevel=2`` and
    Python's default warning filter only shows each unique location
    once per process by default, so we use ``simplefilter("always")``
    in the test).
    """
    module_name = "hh_applicant_tool.services.apply_one"
    # Drop from sys.modules so the warning fires again on reimport.
    sys.modules.pop(module_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(module_name)
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations, (
        "Expected hh_applicant_tool.services.apply_one to emit a "
        "DeprecationWarning on import (issue #55)"
    )
    # Message should mention the new VSA path so users know what to use.
    assert any(
        "ApplicationSubmitSlice" in str(w.message) for w in deprecations
    ), (
        f"Deprecation message should mention ApplicationSubmitSlice; got: {[str(w.message) for w in deprecations]}"
    )


def test_apply_worker_module_emits_deprecation_warning() -> None:
    """``hh_applicant_tool.services.apply_worker`` must warn on import."""
    module_name = "hh_applicant_tool.services.apply_worker"
    sys.modules.pop(module_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(module_name)
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations, (
        "Expected hh_applicant_tool.services.apply_worker to emit a "
        "DeprecationWarning on import (issue #55)"
    )
    assert any("ApplicationSubmitSlice" in str(w.message) for w in deprecations)


@pytest.mark.parametrize(
    "module_name",
    [
        "hh_applicant_tool.services.apply_one",
        "hh_applicant_tool.services.apply_worker",
    ],
)
def test_deprecation_message_mentions_vsa_replacement(module_name: str) -> None:
    """Deprecation messages must guide users to the VSA replacement."""
    sys.modules.pop(module_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(module_name)
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations
    msg = str(deprecations[0].message)
    # Either the module path or the slice class is a valid migration hint.
    assert (
        "ApplicationSubmitSlice" in msg or "job_bot.application_submit" in msg
    ), f"Deprecation message must point to the VSA replacement; got: {msg!r}"
