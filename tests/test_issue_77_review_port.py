"""Deprecation + wiring checks for the review-flow port (issue #77).

The review-flow state machine used to live in
``hh_applicant_tool.services.review_flow``. As part of the VSA
switchover (issue #77) it has been moved to
``job_bot.telegram_bot.services.review_service`` and the legacy module
is kept as a deprecation shim. This test file pins the contract — it
is the analogue of :mod:`tests.test_issue_77_digest_port` for the
review-flow slice.

The contract:

* the VSA path is the source of truth (the legacy shim subclasses it,
  not a re-implementation);
* the legacy :class:`ReviewFlowService` is a subclass of the VSA class
  — ``issubclass`` holds, but the inverse does not;
* the DTOs (:class:`OutgoingMessage`, :class:`InlineButton`) and the
  state/callback-string constants are re-exported as plain names from
  the legacy module — no warning is emitted when they are imported
  (matches the convention in ``applications.py`` / ``relevance.py`` /
  ``cover_letters.py`` where the warning fires on instantiation only);
* instantiating the legacy :class:`ReviewFlowService` emits a
  :class:`DeprecationWarning` pointing at the VSA replacement;
* importing the VSA path or the legacy module emits no deprecation
  warning at import time;
* the ``TelegramBotSlice`` factory builds its default review service
  from the VSA path (not the legacy shim);
* the existing behavioral tests in :mod:`tests.test_review_flow`
  continue to pass via the shim (class identity preserved).
"""

from __future__ import annotations

import importlib
import inspect
import sys
import warnings
from unittest.mock import MagicMock

import pytest


# ─── VSA path is the source of truth ──────────────────────────────


def test_vsa_path_exposes_review_classes_and_constants() -> None:
    """The VSA module exposes the public surface used by the FSM and tests."""
    from job_bot.telegram_bot.services import review_service as vsa
    from job_bot.telegram_bot.services.review_service import (
        CB_CONFIRM_SEND as VsaCb,
    )
    from job_bot.telegram_bot.services.review_service import (
        InlineButton as VsaBtn,
    )
    from job_bot.telegram_bot.services.review_service import (
        OutgoingMessage as VsaMsg,
    )
    from job_bot.telegram_bot.services.review_service import (
        ReviewFlowService as VsaSvc,
    )
    from job_bot.telegram_bot.services.review_service import (
        STATE_IDLE as VsaState,
    )

    assert VsaSvc is vsa.ReviewFlowService
    assert VsaMsg is vsa.OutgoingMessage
    assert VsaBtn is vsa.InlineButton
    assert VsaCb == vsa.CB_CONFIRM_SEND
    assert VsaState == vsa.STATE_IDLE
    # A sampling of the well-known constants exists.
    for name in (
        "STATE_REVIEW_INTRO",
        "STATE_REVIEW_TEST",
        "STATE_REVIEW_COVER",
        "STATE_CONFIRM_APPLY",
        "CB_INTRO_CONTINUE",
        "CB_TEST_OK",
        "CB_COVER_OK",
    ):
        assert hasattr(vsa, name), f"VSA module must expose {name}"


def test_vsa_path_emits_no_deprecation_warning() -> None:
    """Importing the VSA path must not emit any ``DeprecationWarning``."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("job_bot.telegram_bot.services.review_service")
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations == [], (
        "VSA path must not emit DeprecationWarning; got: "
        f"{[str(w.message) for w in deprecations]}"
    )


# ─── Legacy shim is a subclass, not a re-implementation ───────────


def test_legacy_module_is_a_shim_not_reimplementation() -> None:
    """``hh_applicant_tool.services.review_flow`` has no class defs of
    review-flow types.

    The legacy module must subclass the VSA service — if anyone
    re-adds ``class ReviewFlowService(_NotTheVsaClass): ...`` here,
    this test fails and forces a deliberate decision (duplicate
    definition = drift risk).
    """
    legacy = importlib.import_module("hh_applicant_tool.services.review_flow")
    from job_bot.telegram_bot.services.review_service import (
        ReviewFlowService as VsaSvc,
    )

    # The class on the legacy module must be defined LOCALLY (i.e. it
    # is the shim subclass) — not a re-import of the VSA class.
    legacy_cls = legacy.ReviewFlowService
    assert legacy_cls is not VsaSvc, (
        "Legacy shim must subclass the VSA service; got the VSA class "
        "itself, which would mean the warning-on-init subclass is missing."
    )
    # And the shim class must live in the legacy module.
    assert legacy_cls.__module__ == legacy.__name__, (
        f"Legacy shim class must be defined in {legacy.__name__!r}; "
        f"got module {legacy_cls.__module__!r}."
    )
    # The base class of the shim must be the VSA class.
    assert issubclass(legacy_cls, VsaSvc)
    # The reverse must NOT hold: the VSA class is not a subclass of the
    # shim (that would be a confusing cyclic dependency).
    assert not issubclass(VsaSvc, legacy_cls)

    # DTOs / constants are plain re-exports (no local definition of
    # these types in the legacy module).
    defined_here = {
        name
        for name, value in vars(legacy).items()
        if isinstance(value, type) and value.__module__ == legacy.__name__
    }
    public_types = {"OutgoingMessage", "InlineButton"}
    assert not (defined_here & public_types), (
        f"Legacy module must not define DTOs locally; "
        f"found: {defined_here & public_types}."
    )


def test_legacy_first_import_emits_no_deprecation_warning() -> None:
    """First import of the legacy module does NOT emit a DeprecationWarning.

    The warning must fire only on instantiation, matching the
    convention in ``applications.py`` / ``relevance.py`` /
    ``cover_letters.py`` so that test runs are not polluted by every
    ``from hh_applicant_tool.services.review_flow import ...``.

    If the module is already cached (by an earlier test/fixture), we
    trust the first import — re-running it would re-execute the shim's
    module body and create a *new* ``ReviewFlowService`` subclass,
    breaking class identity in subsequent tests that pin it.
    """
    mod_name = "hh_applicant_tool.services.review_flow"
    if mod_name not in sys.modules:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module(mod_name)
        deprecations = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecations == [], (
            "Legacy module must not emit DeprecationWarning at import; got: "
            f"{[str(w.message) for w in deprecations]}"
        )
    # else: the first import (which some earlier test triggered) is
    # the source of truth for this invariant.


def test_legacy_attribute_access_emits_no_deprecation_warning() -> None:
    """Reading a legacy public name does NOT emit a DeprecationWarning.

    Following the repo convention (warning on ``__init__`` only),
    simply accessing ``legacy.ReviewFlowService`` is silent — the
    warning fires when the user actually constructs the service.
    """
    legacy = importlib.import_module("hh_applicant_tool.services.review_flow")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cls = legacy.ReviewFlowService
        msg_type = legacy.OutgoingMessage
        btn_type = legacy.InlineButton
        state = legacy.STATE_IDLE
        cb = legacy.CB_INTRO_CONTINUE
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations == [], (
        "Attribute access on legacy shim must not emit DeprecationWarning; "
        f"got: {[str(w.message) for w in deprecations]}"
    )
    # Sanity: the values are the right ones.
    assert cls.__name__ == "ReviewFlowService"
    assert msg_type.__name__ == "OutgoingMessage"
    assert btn_type.__name__ == "InlineButton"
    assert state == "idle"
    assert cb == "rf:intro:continue"


def test_legacy_instantiation_emits_deprecation_warning() -> None:
    """Constructing a legacy :class:`ReviewFlowService` warns.

    Mirrors the convention in ``applications.py``:
    ``warnings.warn(..., DeprecationWarning, stacklevel=2)`` in
    ``__init__``.
    """
    import sqlite3

    from hh_applicant_tool.storage import StorageFacade
    from hh_applicant_tool.telegram.transport import TelegramTransport
    from job_bot.telegram_bot.services.review_service import (
        ReviewFlowService as VsaSvc,
    )

    legacy = importlib.import_module("hh_applicant_tool.services.review_flow")
    facade = StorageFacade(sqlite3.connect(":memory:"))
    transport = MagicMock(spec=TelegramTransport)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        svc = legacy.ReviewFlowService(
            storage=facade, transport=transport, config={}
        )

    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations, (
        "Expected DeprecationWarning when instantiating the legacy shim."
    )
    msg = str(deprecations[0].message)
    assert "deprecated" in msg
    assert "job_bot.telegram_bot.services.review_service" in msg
    # The shim instance is a real VSA service under the hood.
    assert isinstance(svc, VsaSvc)
    assert type(svc) is legacy.ReviewFlowService  # exact shim class


def test_legacy_unknown_attribute_raises_attribute_error() -> None:
    """Unknown names surface a clear ``AttributeError`` (not a silent miss)."""
    legacy = importlib.import_module("hh_applicant_tool.services.review_flow")
    # ``hasattr`` returns False instead of raising — this avoids both
    # B018 (useless expression) and B009 (getattr-with-constant).
    assert not hasattr(legacy, "zzz_not_a_real_name")


# ─── ``hh_applicant_tool.services`` re-export path still works ─────


def test_services_package_re_exports_via_shim() -> None:
    """``from hh_applicant_tool.services import ReviewFlowService`` still works.

    ``hh_applicant_tool.services.__init__`` does ``from .review_flow
    import ...`` — i.e. it grabs the shim's subclass. The re-exported
    class is the legacy subclass (NOT the VSA class) because the shim
    is a real subclass that injects the deprecation warning.
    """
    from hh_applicant_tool.services import (
        ReviewFlowService as FromPkg,
    )
    from hh_applicant_tool.services.review_flow import (
        CB_INTRO_CONTINUE as FromShimCb,
    )
    from hh_applicant_tool.services.review_flow import (
        InlineButton as FromShimBtn,
    )
    from hh_applicant_tool.services.review_flow import (
        OutgoingMessage as FromShimMsg,
    )
    from hh_applicant_tool.services.review_flow import (
        ReviewFlowService as FromShimCls,
    )
    from hh_applicant_tool.services.review_flow import (
        STATE_IDLE as FromShimState,
    )
    from job_bot.telegram_bot.services.review_service import (
        InlineButton as VsaBtn,
    )
    from job_bot.telegram_bot.services.review_service import (
        OutgoingMessage as VsaMsg,
    )
    from job_bot.telegram_bot.services.review_service import (
        ReviewFlowService as VsaSvc,
    )

    # DTOs / constants are the same object across all import paths.
    # (Note: ``hh_applicant_tool.services`` historically only
    # re-exported ``ReviewFlowService`` — not the DTOs or constants.
    # This test mirrors that contract: package-root re-export is for
    # the class only; DTOs/constants come from the submodule.)
    assert FromShimMsg is VsaMsg
    assert FromShimBtn is VsaBtn
    assert FromShimState == "idle"
    assert FromShimCb == "rf:intro:continue"

    # The class identity is the shim subclass, and it must be the
    # same object whether imported from the package root or from the
    # submodule (the ``is`` check in older tests).
    assert FromPkg is FromShimCls
    # And the shim subclass is a real subclass of the VSA class.
    assert issubclass(FromPkg, VsaSvc)


# ─── TelegramBotSlice wires up the VSA path ───────────────────────


def test_telegram_bot_slice_factory_uses_vsa_path() -> None:
    """The slice's ``_default_review_service`` factory imports from VSA.

    Structural check: the factory source must
    ``import job_bot.telegram_bot.services.review_service`` (the VSA
    path) and NOT ``hh_applicant_tool.services.review_flow`` (the
    legacy shim). The factory docstring is allowed to mention the
    legacy path as historical context.
    """
    from job_bot.telegram_bot import slice as slice_mod

    src = inspect.getsource(slice_mod._default_review_service)
    # The actual import statement must be from the VSA path.
    assert "from job_bot.telegram_bot.services.review_service import" in src, (
        "_default_review_service must import from the VSA path "
        "(job_bot.telegram_bot.services.review_service), not the legacy "
        "shim. See issue #77."
    )
    assert "from hh_applicant_tool.services.review_flow import" not in src, (
        "_default_review_service must not import from the legacy shim. "
        "See issue #77."
    )


def test_default_review_service_returns_vsa_class() -> None:
    """Calling ``_default_review_service`` returns a real ``ReviewFlowService``.

    The factory takes a RAW ``sqlite3.Connection`` (matching how the
    slice is built in production — see
    ``TelegramBotSlice._resolve_storage``) and wraps it in
    :class:`StorageFacade` internally. Passing a pre-built facade
    would double-wrap and the inner ``PRAGMA`` would fail.
    """
    import sqlite3
    from unittest.mock import MagicMock

    from hh_applicant_tool.telegram.transport import TelegramTransport
    from job_bot.telegram_bot.services.review_service import (
        ReviewFlowService as VsaSvc,
    )
    from job_bot.telegram_bot.slice import _default_review_service

    conn = sqlite3.connect(":memory:")
    transport = MagicMock(spec=TelegramTransport)
    svc = _default_review_service(conn, transport, config={})
    assert isinstance(svc, VsaSvc)
    assert type(svc) is VsaSvc  # exact VSA class, not the legacy subclass


# ─── Defensive: the VSA service is a real, callable class ──────────


def test_vsa_service_can_be_constructed_with_minimal_args() -> None:
    """Smoke-test: the VSA service still accepts the same DI args as the shim."""
    import sqlite3
    from unittest.mock import MagicMock

    from hh_applicant_tool.storage import StorageFacade
    from hh_applicant_tool.telegram.transport import TelegramTransport
    from job_bot.telegram_bot.services.review_service import ReviewFlowService

    facade = StorageFacade(sqlite3.connect(":memory:"))
    transport = MagicMock(spec=TelegramTransport)
    svc = ReviewFlowService(storage=facade, transport=transport)
    assert svc.clock is not None  # fallback SystemClock
    assert svc.storage is not None


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
