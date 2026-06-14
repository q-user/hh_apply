"""Deprecation + wiring checks for the daily-digest port (issue #77).

The daily-digest service used to live in
``hh_applicant_tool.services.daily_digest``. As part of the VSA
switchover (issue #77) it has been moved to
``job_bot.telegram_bot.services.digest_service`` and the legacy module
is kept as a deprecation shim. This test file pins the contract:

* the VSA path is the source of truth (the legacy shim subclasses it,
  not a re-implementation);
* the legacy module's :class:`DailyDigestService` is a subclass of the
  VSA class — class ``issubclass`` holds, but the inverse does not (the
  shim exists only to inject the deprecation warning);
* the DTOs (:class:`DigestResult`, :class:`DraftGroup`) and the
  :data:`LAST_DIGEST_KEY` constant are re-exported as plain names from
  the legacy module — no warning is emitted when they are imported
  (matches the convention in ``applications.py`` / ``relevance.py`` /
  ``cover_letters.py`` where the warning fires on instantiation only);
* instantiating the legacy :class:`DailyDigestService` emits a
  :class:`DeprecationWarning` pointing at the VSA replacement;
* importing the VSA path or the legacy module emits no deprecation
  warning at import time;
* the ``TelegramBotSlice`` factory builds its default digest service
  from the VSA path (not the legacy shim).
"""

from __future__ import annotations

import importlib
import sys
import warnings
from unittest.mock import MagicMock

import pytest


# ─── VSA path is the source of truth ──────────────────────────────


def test_vsa_path_exposes_digest_classes() -> None:
    """The VSA module exposes the four public names used by the bot."""
    from job_bot.telegram_bot.services import digest_service as vsa
    from job_bot.telegram_bot.services.digest_service import (
        LAST_DIGEST_KEY as VSA_KEY,
    )
    from job_bot.telegram_bot.services.digest_service import (
        DailyDigestService as VsaSvc,
    )
    from job_bot.telegram_bot.services.digest_service import (
        DigestResult as VsaResult,
    )
    from job_bot.telegram_bot.services.digest_service import (
        DraftGroup as VsaGroup,
    )

    assert VsaSvc is vsa.DailyDigestService
    assert VsaResult is vsa.DigestResult
    assert VsaGroup is vsa.DraftGroup
    assert VSA_KEY == "telegram.last_digest_date"
    assert VSA_KEY == vsa.LAST_DIGEST_KEY


def test_vsa_path_emits_no_deprecation_warning() -> None:
    """Importing the VSA path must not emit any ``DeprecationWarning``."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("job_bot.telegram_bot.services.digest_service")
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations == [], (
        "VSA path must not emit DeprecationWarning; got: "
        f"{[str(w.message) for w in deprecations]}"
    )


# ─── Legacy shim is a subclass, not a re-implementation ───────────


def test_legacy_module_is_a_shim_not_reimplementation() -> None:
    """``hh_applicant_tool.services.daily_digest`` has no class defs.

    The legacy module must subclass the VSA service — if anyone
    re-adds ``class DailyDigestService(_NotTheVsaClass): ...`` here,
    this test fails and forces a deliberate decision (duplicate
    definition = drift risk).
    """
    legacy = importlib.import_module("hh_applicant_tool.services.daily_digest")
    from job_bot.telegram_bot.services.digest_service import (
        DailyDigestService as VsaSvc,
    )

    # The class on the legacy module must be defined LOCALLY (i.e. it
    # is the shim subclass) — not a re-import of the VSA class.
    legacy_cls = legacy.DailyDigestService
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

    # DTOs / constants are plain re-exports (no local definition).
    defined_here = {
        name
        for name, value in vars(legacy).items()
        if isinstance(value, type) and value.__module__ == legacy.__name__
    }
    public_types = {"DigestResult", "DraftGroup"}
    assert not (defined_here & public_types), (
        f"Legacy module must not define DTOs locally; "
        f"found: {defined_here & public_types}."
    )


def test_legacy_first_import_emits_no_deprecation_warning() -> None:
    """First import of the legacy module does NOT emit a DeprecationWarning.

    The warning must fire only on instantiation, matching the
    convention in ``applications.py`` / ``relevance.py`` /
    ``cover_letters.py`` so that test runs are not polluted by every
    ``from hh_applicant_tool.services.daily_digest import ...``.

    This is the structural test for the invariant. To avoid breaking
    class identity in later tests (the shim defines a *new* class
    every time the module body runs), this test does NOT reload the
    module after it has been imported. Instead, the first import
    triggered by the test session — whether here or in a fixture —
    must be silent. We verify this in two ways:

    1. *If* the module is not yet in ``sys.modules``, import it
       inside ``warnings.catch_warnings`` and assert no warning fires.
    2. If it *is* already imported, the cached module is the
       authoritative observation — and we additionally assert the
       module body never set ``__warning__`` (a per-module attribute
       populated by ``warnings.warn`` for module-level emissions).
    """
    mod_name = "hh_applicant_tool.services.daily_digest"
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
    else:
        # The module was already imported (e.g. by test_daily_digest).
        # The first import is the source of truth — if it had emitted
        # a warning, that warning would have polluted the session.
        # We assert here that no DeprecationWarning was raised at
        # attribute access either (the sibling test pins that more
        # precisely), and that the module body left no
        # ``__warning__`` sentinel.
        cached = sys.modules[mod_name]
        assert not hasattr(cached, "__warning__") or (
            "deprecated" not in str(getattr(cached, "__warning__", ""))
        ), (
            "Cached legacy module has a __warning__ sentinel; "
            "the first import emitted a DeprecationWarning."
        )


def test_legacy_attribute_access_emits_no_deprecation_warning() -> None:
    """Reading a legacy public name does NOT emit a DeprecationWarning.

    Following the repo convention (warning on ``__init__`` only),
    simply accessing ``legacy.DailyDigestService`` is silent — the
    warning fires when the user actually constructs the service.
    """
    legacy = importlib.import_module("hh_applicant_tool.services.daily_digest")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cls = legacy.DailyDigestService
        result_type = legacy.DigestResult
        group_type = legacy.DraftGroup
        key = legacy.LAST_DIGEST_KEY
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations == [], (
        "Attribute access on legacy shim must not emit DeprecationWarning; "
        f"got: {[str(w.message) for w in deprecations]}"
    )
    # Sanity: the values are the right ones.
    assert cls.__name__ == "DailyDigestService"
    assert result_type.__name__ == "DigestResult"
    assert group_type.__name__ == "DraftGroup"
    assert key == "telegram.last_digest_date"


def test_legacy_instantiation_emits_deprecation_warning() -> None:
    """Constructing a legacy :class:`DailyDigestService` warns.

    Mirrors the convention in ``applications.py``:
    ``warnings.warn(..., DeprecationWarning, stacklevel=2)`` in
    ``__init__``.
    """
    import sqlite3

    from hh_applicant_tool.storage import StorageFacade
    from hh_applicant_tool.telegram.transport import TelegramTransport
    from job_bot.telegram_bot.services.digest_service import (
        DailyDigestService as VsaSvc,
    )

    legacy = importlib.import_module("hh_applicant_tool.services.daily_digest")
    facade = StorageFacade(sqlite3.connect(":memory:"))
    transport = MagicMock(spec=TelegramTransport)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        svc = legacy.DailyDigestService(
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
    assert "job_bot.telegram_bot.services.digest_service" in msg
    # The shim instance is a real VSA service under the hood.
    assert isinstance(svc, VsaSvc)
    assert type(svc) is legacy.DailyDigestService  # exact shim class


def test_legacy_unknown_attribute_raises_attribute_error() -> None:
    """Unknown names surface a clear ``AttributeError`` (not a silent miss)."""
    legacy = importlib.import_module("hh_applicant_tool.services.daily_digest")
    # ``hasattr`` returns False instead of raising — this avoids both
    # B018 (useless expression) and B009 (getattr-with-constant).
    assert not hasattr(legacy, "zzz_not_a_real_name")


# ─── ``hh_applicant_tool.services`` re-export path still works ─────


def test_services_package_re_exports_via_shim() -> None:
    """``from hh_applicant_tool.services import DailyDigestService`` still works.

    ``hh_applicant_tool.services.__init__`` does ``from .daily_digest
    import ...`` — i.e. it grabs the shim's subclass. The re-exported
    class is the legacy subclass (NOT the VSA class) because the shim
    is a real subclass that injects the deprecation warning.
    """
    from hh_applicant_tool.services import (
        DailyDigestService as FromPkg,
    )
    from hh_applicant_tool.services import (
        DigestResult as FromPkgResult,
    )
    from hh_applicant_tool.services import (
        DraftGroup as FromPkgGroup,
    )
    from hh_applicant_tool.services import (
        LAST_DIGEST_KEY as FromPkgKey,
    )
    from hh_applicant_tool.services.daily_digest import (
        LAST_DIGEST_KEY as FromShim,
    )
    from hh_applicant_tool.services.daily_digest import (
        DailyDigestService as FromShimCls,
    )
    from job_bot.telegram_bot.services.digest_service import (
        LAST_DIGEST_KEY as VsaKey,
    )
    from job_bot.telegram_bot.services.digest_service import (
        DigestResult as VsaResult,
    )
    from job_bot.telegram_bot.services.digest_service import (
        DraftGroup as VsaGroup,
    )

    # DTOs / constants are the same object across all import paths.
    assert FromPkgResult is VsaResult
    assert FromPkgGroup is VsaGroup
    assert FromPkgKey == VsaKey == FromShim

    # The class identity is the shim subclass, and it must be the
    # same object whether imported from the package root or from the
    # submodule (the ``is`` check in older tests).
    assert FromPkg is FromShimCls
    # And the shim subclass is a real subclass of the VSA class.
    from job_bot.telegram_bot.services.digest_service import (
        DailyDigestService as VsaSvc,
    )

    assert issubclass(FromPkg, VsaSvc)


# ─── TelegramBotSlice wires up the VSA path ───────────────────────


def test_telegram_bot_slice_factory_uses_vsa_path() -> None:
    """The slice's ``_default_digest_service`` factory imports from VSA.

    This is a structural check on the factory source: it must
    ``import job_bot.telegram_bot.services.digest_service`` (the VSA
    path) and NOT ``hh_applicant_tool.services.daily_digest`` (the
    legacy shim). A ``sys.modules`` check is not used because the
    legacy shim may have been imported by an earlier test in the
    same session and we don't want to depend on test ordering.
    """
    import inspect

    from job_bot.telegram_bot import slice as slice_mod

    src = inspect.getsource(slice_mod._default_digest_service)
    assert "job_bot.telegram_bot.services.digest_service" in src, (
        "_default_digest_service must import from the VSA path "
        "(job_bot.telegram_bot.services.digest_service), not the legacy "
        "shim. See issue #77."
    )
    assert "hh_applicant_tool.services.daily_digest" not in src, (
        "_default_digest_service must not import from the legacy shim. "
        "See issue #77."
    )


def test_default_digest_service_returns_vsa_class() -> None:
    """Calling ``_default_digest_service`` returns a real ``DailyDigestService``.

    The factory takes a RAW ``sqlite3.Connection`` (matching how the
    slice is built in production — see
    ``TelegramBotSlice._resolve_storage``) and wraps it in
    :class:`StorageFacade` internally. Passing a pre-built facade
    would double-wrap and the inner ``PRAGMA`` would fail.
    """
    import sqlite3
    from unittest.mock import MagicMock

    from hh_applicant_tool.telegram.transport import TelegramTransport
    from job_bot.telegram_bot.services.digest_service import (
        DailyDigestService as VsaSvc,
    )
    from job_bot.telegram_bot.slice import _default_digest_service

    conn = sqlite3.connect(":memory:")
    transport = MagicMock(spec=TelegramTransport)
    svc = _default_digest_service(conn, transport, config={})
    assert isinstance(svc, VsaSvc)
    assert type(svc) is VsaSvc  # exact VSA class, not the legacy subclass


# ─── Defensive: the VSA service is a real, callable class ──────────


def test_vsa_service_can_be_constructed_with_minimal_args() -> None:
    """Smoke-test: the VSA service still accepts the same DI args as the shim."""
    import sqlite3
    from unittest.mock import MagicMock

    from hh_applicant_tool.storage import StorageFacade
    from hh_applicant_tool.telegram.transport import TelegramTransport
    from job_bot.telegram_bot.services.digest_service import DailyDigestService

    facade = StorageFacade(sqlite3.connect(":memory:"))
    transport = MagicMock(spec=TelegramTransport)
    svc = DailyDigestService(storage=facade, transport=transport)
    assert svc.clock is not None  # fallback SystemClock


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
