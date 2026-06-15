"""Tests for the VSA port of ``hh_applicant_tool.api.errors`` to
``job_bot.shared.api.errors`` (issue #152).

Contract:

* The shared :class:`ApiError` and its *generic* subclasses
  (``BadResponse``, ``Redirect``, ``ClientError``, ``BadRequest``,
  ``Forbidden``, ``ResourceNotFound``, ``InternalServerError``,
  ``BadGateway``) live in :mod:`job_bot.shared.api.errors`.
* The slice-specific subclasses :class:`CaptchaRequired` and
  :class:`LimitExceeded` live in
  :mod:`job_bot.application_submit.errors` (alongside
  :class:`RetryableError` / :class:`FatalError`).
* The legacy ``hh_applicant_tool.api.errors`` module and the
  ``hh_applicant_tool.api`` package re-exports both keep working for
  one release window and emit a single ``DeprecationWarning`` on
  import (per the canonical deprecation contract in
  ``tests/test_issue_92_deprecation.py``).
"""

from __future__ import annotations

import importlib
import sys
import warnings
from typing import Any

import pytest
import requests

# The shared / generic exception classes.
SHARED_EXCEPTIONS: tuple[str, ...] = (
    "BadResponse",
    "ApiError",
    "Redirect",
    "ClientError",
    "BadRequest",
    "Forbidden",
    "ResourceNotFound",
    "InternalServerError",
    "BadGateway",
)

# Slice-specific exceptions that move to ``application_submit.errors``.
SLICE_EXCEPTIONS: tuple[str, ...] = (
    "CaptchaRequired",
    "LimitExceeded",
)


# ─── New canonical location: shared errors ───────────────────────


def test_new_shared_errors_module_is_importable() -> None:
    """``job_bot.shared.api.errors`` exists."""
    module = importlib.import_module("job_bot.shared.api.errors")
    assert module is not None


@pytest.mark.parametrize("name", SHARED_EXCEPTIONS)
def test_shared_errors_classes_importable(name: str) -> None:
    """Every shared exception class is exported from the new module."""
    module = importlib.import_module("job_bot.shared.api.errors")
    assert hasattr(module, name), (
        f"job_bot.shared.api.errors is missing {name!r}"
    )


def test_shared_errors_have_correct_subclass_relationships() -> None:
    """The exception hierarchy is preserved verbatim.

    Subclass relationships matter because call sites use ``except``
    on the base classes to catch a family of API failures.
    """
    from job_bot.shared.api.errors import (
        ApiError,
        BadGateway,
        BadRequest,
        BadResponse,
        ClientError,
        Forbidden,
        InternalServerError,
        Redirect,
        ResourceNotFound,
    )

    # All concrete exception classes must be subclasses of ``BadResponse`` so
    # the existing ``except BadResponse`` blocks keep working.
    for cls in (
        ApiError,
        Redirect,
        ClientError,
        BadRequest,
        Forbidden,
        ResourceNotFound,
        InternalServerError,
        BadGateway,
    ):
        assert issubclass(cls, BadResponse), (
            f"{cls.__name__} must subclass BadResponse"
        )

    # Sub-hierarchy matches the legacy module.
    assert issubclass(Redirect, ApiError)
    assert issubclass(ClientError, ApiError)
    assert issubclass(BadRequest, ClientError)
    assert issubclass(Forbidden, ClientError)
    assert issubclass(ResourceNotFound, ClientError)
    assert issubclass(InternalServerError, ApiError)
    assert issubclass(BadGateway, InternalServerError)


def test_apierror_raise_for_status_dispatches_correctly() -> None:
    """``ApiError.raise_for_status`` produces the right subclass for each code.

    This is the core of the ``errors.py`` API and we want to make sure
    it round-trips through the move.
    """
    from job_bot.shared.api.errors import (
        ApiError,
        BadGateway,
        BadRequest,
        Forbidden,
        Redirect,
        ResourceNotFound,
    )

    def _fake_response(status: int) -> requests.Response:
        resp = requests.Response()
        resp.status_code = status
        resp.url = "https://api.hh.ru/vacancies"
        return resp

    with pytest.raises(Redirect):
        ApiError.raise_for_status(_fake_response(302), {})

    with pytest.raises(BadRequest):
        ApiError.raise_for_status(_fake_response(400), {})

    with pytest.raises(Forbidden):
        ApiError.raise_for_status(_fake_response(403), {})

    with pytest.raises(ResourceNotFound):
        ApiError.raise_for_status(_fake_response(404), {})

    with pytest.raises(BadGateway):
        ApiError.raise_for_status(_fake_response(502), {})


# ─── Slice-specific errors ───────────────────────────────────────


def test_submit_specific_in_application_submit_errors() -> None:
    """``CaptchaRequired`` and ``LimitExceeded`` live in ``application_submit.errors``."""
    module = importlib.import_module("job_bot.application_submit.errors")
    assert hasattr(module, "CaptchaRequired")
    assert hasattr(module, "LimitExceeded")


def test_submit_specific_extend_client_error() -> None:
    """``CaptchaRequired`` and ``LimitExceeded`` extend ``ClientError``."""
    from job_bot.application_submit.errors import (
        CaptchaRequired,
        LimitExceeded,
    )
    from job_bot.shared.api.errors import ClientError

    assert issubclass(CaptchaRequired, ClientError)
    assert issubclass(LimitExceeded, ClientError)


def test_captcha_required_extracts_captcha_url() -> None:
    """``CaptchaRequired.captcha_url`` extracts the URL from the payload."""
    from job_bot.application_submit.errors import CaptchaRequired

    response = requests.Response()
    response.status_code = 403
    response.url = "https://api.hh.ru/negotiations"
    data: dict[str, Any] = {
        "errors": [
            {
                "type": "captcha_required",
                "value": "captcha_required",
                "captcha_url": "https://captcha.hh.ru/captcha/abc",
            }
        ]
    }
    err = CaptchaRequired(response, data)
    assert err.captcha_url == "https://captcha.hh.ru/captcha/abc"
    assert "captcha.hh.ru" in err.message


# ─── Legacy import paths (one release window) ────────────────────


def test_legacy_errors_module_still_works() -> None:
    """``hh_applicant_tool.api.errors`` keeps working (one release window)."""
    module = importlib.import_module("hh_applicant_tool.api.errors")
    assert hasattr(module, "ApiError")
    assert hasattr(module, "CaptchaRequired")
    assert hasattr(module, "LimitExceeded")


def test_legacy_errors_module_uses_canonical_deprecation_warning() -> None:
    """Importing the legacy errors module emits a DeprecationWarning.

    The message format follows the canonical contract
    ("<module> is deprecated; use <vsa> instead (issue #<N>).").
    """
    sys.modules.pop("hh_applicant_tool.api.errors", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("hh_applicant_tool.api.errors")

    matches = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "hh_applicant_tool.api.errors" in str(w.message)
        and "job_bot.shared.api.errors" in str(w.message)
        and "issue #152" in str(w.message)
    ]
    assert matches, (
        "expected a DeprecationWarning for hh_applicant_tool.api.errors; "
        f"got: {[str(w.message) for w in caught]}"
    )


def test_legacy_errors_warning_message_matches_canonical_contract() -> None:
    """The errors submodule's warning message matches ``CONTRACT_RE``.

    The ``CaptchaRequired`` / ``LimitExceeded`` exception classes are
    re-exported by this shim, but they actually live in
    ``job_bot.application_submit.errors`` — the contract regex only
    points at the *primary* VSA target (``job_bot.shared.api.errors``)
    and the second-step migration note lives in the shim's docstring.
    """
    from tests.test_issue_92_deprecation import CONTRACT_RE

    sys.modules.pop("hh_applicant_tool.api.errors", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("hh_applicant_tool.api.errors")

    matches = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "hh_applicant_tool.api.errors" in str(w.message)
    ]
    assert matches, "no DeprecationWarning for the errors shim was captured"
    message = str(matches[0].message)

    match = CONTRACT_RE.match(message)
    assert match is not None, (
        f"errors shim's message does not match the canonical contract: {message!r}"
    )
    assert match.group("module") == "hh_applicant_tool.api.errors"
    assert match.group("vsa") == "job_bot.shared.api.errors"
    assert match.group("issue") == "152"


def test_legacy_api_package_plain_import_emits_no_warning() -> None:
    """A plain ``import hh_applicant_tool.api`` does not emit any DeprecationWarning.

    The package is a :pep:`562` lazy re-export; the warning fires
    only on attribute access. This guards against regressing to a
    top-level ``warnings.warn`` that would pollute every test run
    (and every production ``from . import api`` call).
    """
    sys.modules.pop("hh_applicant_tool.api", None)
    sys.modules.pop("hh_applicant_tool.api.datatypes", None)
    sys.modules.pop("hh_applicant_tool.api.errors", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("hh_applicant_tool.api")

    package_warnings = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "hh_applicant_tool.api" in str(w.message)
        and "issue #152" in str(w.message)
    ]
    assert package_warnings == [], (
        "plain import of hh_applicant_tool.api should NOT emit a "
        f"DeprecationWarning; got: {[str(w.message) for w in package_warnings]}"
    )


def test_legacy_api_package_emits_canonical_warning_on_attribute_access() -> (
    None
):
    """Attribute access on the lazy package shim fires exactly one package warning.

    We pre-load the errors submodule so its own deprecation shim is
    cached in ``sys.modules``; otherwise the package shim's
    ``importlib.import_module`` call would chain into the errors
    submodule's module-level ``warnings.warn`` and produce a second
    warning.  The contract we care about is *the package shim's*
    warning — exactly one, with the canonical message.
    """
    # Pre-load the errors submodule so its own module-level warn
    # is consumed before we exercise the package shim.
    importlib.import_module("hh_applicant_tool.api.errors")

    sys.modules.pop("hh_applicant_tool.api", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # Plain import of the package — no warning expected.
        importlib.import_module("hh_applicant_tool.api")
        # Attribute access fires the package shim's warning.
        pkg = sys.modules["hh_applicant_tool.api"]
        _ = pkg.BadResponse

    package_warnings = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "hh_applicant_tool.api" in str(w.message)
        and "issue #152" in str(w.message)
    ]
    assert len(package_warnings) == 1, (
        "expected exactly one DeprecationWarning from the package shim; "
        f"got: {[str(w.message) for w in package_warnings]}"
    )


def test_legacy_api_package_from_import_emits_one_package_warning() -> None:
    """``from hh_applicant_tool.api import BadResponse`` emits exactly one package warning.

    CPython's import machinery calls :pep:`562` ``__getattr__`` *twice*
    for the ``from X import Y`` form: once via :func:`getattr` to fetch
    the value, and a second time via :func:`hasattr` from
    :func:`importlib._handle_fromlist` (only when the name is in
    ``__all__``).  The package shim caches the resolved attribute in
    its ``__dict__`` so the second lookup short-circuits and the
    warning is emitted only once per import statement.

    The errors submodule's own module-level warning is captured in
    the same catch context (it fires when the package shim imports
    the submodule on first access).  We assert the submodule warning
    separately so the test is robust against the submodule being
    pre-cached by other tests.
    """
    # Force a fresh state so the submodule's module-level warning
    # fires inside our catch context (not before it).
    importlib.invalidate_caches()
    sys.modules.pop("hh_applicant_tool.api", None)
    sys.modules.pop("hh_applicant_tool.api.errors", None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # The actual production call site pattern: a ``from`` import.
        from hh_applicant_tool.api import BadResponse  # noqa: F401

    # The package shim's warning fires exactly once, even though
    # CPython calls __getattr__ twice for the from-import form.
    package_warnings = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "hh_applicant_tool.api is deprecated" in str(w.message)
    ]
    assert len(package_warnings) == 1, (
        "expected exactly one DeprecationWarning from the package shim "
        "for a from-import; got: "
        f"{[str(w.message) for w in package_warnings]}"
    )

    # The errors submodule's own module-level warning fires once
    # per process (when the submodule is freshly imported, not when
    # it is already cached in sys.modules).
    submodule_warnings = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "hh_applicant_tool.api.errors is deprecated" in str(w.message)
    ]
    assert len(submodule_warnings) == 1, (
        "expected exactly one DeprecationWarning from the errors submodule "
        "(fired once per process when the submodule is freshly imported); "
        f"got: {[str(w.message) for w in submodule_warnings]}"
    )


def test_legacy_api_package_warning_message_matches_canonical_contract() -> (
    None
):
    """The package shim's warning message matches ``CONTRACT_RE``.

    The canonical regex (in ``tests/test_issue_92_deprecation.py``)
    is anchored at both ends with ``^...$``, so trailing prose would
    break the match.  The ``CaptchaRequired`` / ``LimitExceeded``
    second-step migration note lives in the module docstring, not
    in the warning message.
    """
    # Import the contract regex from the canonical contract test
    # (read-only reference — we don't modify the contract test).
    from tests.test_issue_92_deprecation import CONTRACT_RE

    # Pre-load the errors submodule to isolate the package shim's
    # warning in the catch context (see above).
    importlib.import_module("hh_applicant_tool.api.errors")

    sys.modules.pop("hh_applicant_tool.api", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("hh_applicant_tool.api")
        pkg = sys.modules["hh_applicant_tool.api"]
        _ = pkg.BadResponse

    package_warnings = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "hh_applicant_tool.api" in str(w.message)
    ]
    assert package_warnings, "no package-shim DeprecationWarning was captured"
    message = str(package_warnings[0].message)

    match = CONTRACT_RE.match(message)
    assert match is not None, (
        f"package shim's message does not match the canonical contract "
        f"template: {message!r}\n"
        f"Expected: '<module.path> is deprecated; use <vsa.path> instead "
        f"(issue #<N>).'"
    )
    assert match.group("module") == "hh_applicant_tool.api"
    assert match.group("vsa") == "job_bot.shared.api"
    assert match.group("issue") == "152"


def test_legacy_errors_reexports_match_canonical_symbols() -> None:
    """Re-exports from the legacy path point at the same classes as the new path."""
    legacy_errors = importlib.import_module("hh_applicant_tool.api.errors")
    new_shared = importlib.import_module("job_bot.shared.api.errors")
    new_submit = importlib.import_module("job_bot.application_submit.errors")

    assert legacy_errors.ApiError is new_shared.ApiError
    assert legacy_errors.BadRequest is new_shared.BadRequest
    assert legacy_errors.CaptchaRequired is new_submit.CaptchaRequired
    assert legacy_errors.LimitExceeded is new_submit.LimitExceeded
