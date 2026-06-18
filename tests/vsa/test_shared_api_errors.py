"""Tests for the VSA port of ``job_bot.shared.api.errors`` to
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

After issue #158 the legacy distribution
package is deleted, so the canonical deprecation-shim tests for the
old import paths are removed (see ``tests/test_issue_92_deprecation.py``
for the surviving canonical warning-format contract).
"""

from __future__ import annotations

import importlib
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
