"""Tests for issue #56: deprecation warnings on the legacy Telegram transport.

The legacy ``hh_applicant_tool.telegram`` module is being replaced by
the VSA slice at ``job_bot.telegram_bot.slice.TelegramBotSlice``. The
legacy modules are kept for backward compatibility but must emit
``DeprecationWarning`` on import so downstream code can find the
deprecation via Python's default warning machinery.
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest


def test_telegram_transport_module_emits_deprecation_warning() -> None:
    """``hh_applicant_tool.telegram.transport`` must warn on import.

    Forced reload ensures the test catches the warning even if the
    module was already imported by an earlier test in the same
    ``pytest`` run (the import is guarded by ``stacklevel=2`` and
    Python's default warning filter only shows each unique location
    once per process by default, so we use ``simplefilter("always")``
    in the test).
    """
    module_name = "hh_applicant_tool.telegram.transport"
    # Drop from sys.modules so the warning fires again on reimport.
    sys.modules.pop(module_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(module_name)
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations, (
        "Expected hh_applicant_tool.telegram.transport to emit a "
        "DeprecationWarning on import (issue #56)"
    )
    # Message should mention the new VSA path so users know what to use.
    assert any("TelegramBotSlice" in str(w.message) for w in deprecations), (
        f"Deprecation message should mention TelegramBotSlice; got: {[str(w.message) for w in deprecations]}"
    )


def test_telegram_package_emits_deprecation_warning() -> None:
    """``import hh_applicant_tool.telegram`` must trigger the warning.

    The package ``__init__`` re-exports ``TelegramTransport`` from
    ``.transport``; the warning is emitted by the underlying module
    when the re-export happens, so any import of the package surfaces
    it.
    """
    module_name = "hh_applicant_tool.telegram"
    # Drop the package and its submodule from sys.modules so the
    # warning fires again on reimport.
    sys.modules.pop("hh_applicant_tool.telegram.transport", None)
    sys.modules.pop(module_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(module_name)
    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations, (
        "Expected ``import hh_applicant_tool.telegram`` to emit a "
        "DeprecationWarning (issue #56)"
    )


@pytest.mark.parametrize(
    "module_name",
    [
        "hh_applicant_tool.telegram",
        "hh_applicant_tool.telegram.transport",
    ],
)
def test_deprecation_message_mentions_vsa_replacement(module_name: str) -> None:
    """Deprecation messages must guide users to the VSA replacement."""
    # Drop from sys.modules so the warning fires again on reimport.
    sys.modules.pop("hh_applicant_tool.telegram.transport", None)
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
    assert "TelegramBotSlice" in msg or "job_bot.telegram_bot" in msg, (
        f"Deprecation message must point to the VSA replacement; got: {msg!r}"
    )


def test_legacy_transport_remains_callable() -> None:
    """The legacy ``TelegramTransport`` stays importable and instantiable.

    Backward compatibility is the whole point of the deprecation —
    downstream code that imports ``TelegramTransport`` should still
    work, just with a ``DeprecationWarning`` on the import.
    """
    from hh_applicant_tool.telegram import (
        TelegramTransport,
        TelegramTransportConfig,
    )

    config = TelegramTransportConfig(
        bot_token="test-token",
        poll_timeout=30,
        allowed_user_ids=(),
    )
    # We don't actually call ``get_updates`` (would hit the network);
    # we only verify that construction succeeds.
    transport = TelegramTransport(config=config)
    assert transport.poll_timeout == 30
    assert transport.allowed_user_ids == ()
