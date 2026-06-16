"""Legacy ``hh_applicant_tool.api`` package — DEPRECATED (issue #152).

New code should import directly from :mod:`job_bot.shared.api`. The
:class:`CaptchaRequired` and :class:`LimitExceeded` exception classes
have additionally moved to :mod:`job_bot.application_submit.errors`
(alongside the slice's :class:`RetryableError` / :class:`FatalError`).

The package is implemented as a :pep:`562` lazy re-export so a plain
``import hh_applicant_tool.api`` does **not** emit a deprecation
warning; the canonical ``use job_bot.shared.api instead`` warning
fires only on attribute access (e.g.
``from hh_applicant_tool.api import BadResponse``).  See
``tests/test_issue_92_deprecation.py`` for the contract regex.
"""

from __future__ import annotations

import importlib
import sys
import warnings
from typing import Any

# Canonical deprecation message.  Format must match the regex in
# ``tests/test_issue_92_deprecation.py::CONTRACT_RE`` (anchored at
# both ends), so the second-step migration note (CaptchaRequired /
# LimitExceeded -> application_submit.errors) lives in the module
# docstring, not in the message itself.
_DEPRECATION_MESSAGE = (
    "hh_applicant_tool.api is deprecated; "
    "use job_bot.shared.api instead (issue #152)."
)

# Submodule name -> dotted legacy module path.  Accessing these
# attributes (e.g. ``hh_applicant_tool.api.datatypes``) imports the
# legacy submodule; the submodule has its own deprecation shim and
# fires its own ``DeprecationWarning`` on import.  The package shim
# only adds the *package* warning.
_LAZY_SUBMODULES: dict[str, str] = {
    "datatypes": "hh_applicant_tool.api.datatypes",
    "errors": "hh_applicant_tool.api.errors",
}

# Symbol name -> ``module.attr`` reference for the
# ``from hh_applicant_tool.api import X`` access pattern.  Preserves
# the public surface of the legacy package for one release window.
_RAW_SYMBOLS: dict[str, str] = {
    # From the legacy ``hh_applicant_tool.api.errors`` module.
    "ApiError": "hh_applicant_tool.api.errors.ApiError",
    "BadGateway": "hh_applicant_tool.api.errors.BadGateway",
    "BadRequest": "hh_applicant_tool.api.errors.BadRequest",
    "BadResponse": "hh_applicant_tool.api.errors.BadResponse",
    "CaptchaRequired": "hh_applicant_tool.api.errors.CaptchaRequired",
    "ClientError": "hh_applicant_tool.api.errors.ClientError",
    "Forbidden": "hh_applicant_tool.api.errors.Forbidden",
    "InternalServerError": "hh_applicant_tool.api.errors.InternalServerError",
    "LimitExceeded": "hh_applicant_tool.api.errors.LimitExceeded",
    "Redirect": "hh_applicant_tool.api.errors.Redirect",
    "ResourceNotFound": "hh_applicant_tool.api.errors.ResourceNotFound",
    # From the legacy ``hh_applicant_tool.api.datatypes`` module.
    "PaginatedItems": "hh_applicant_tool.api.datatypes.PaginatedItems",
    "Resume": "hh_applicant_tool.api.datatypes.Resume",
}


def __getattr__(name: str) -> Any:  # PEP 562
    """Lazy re-export hook: fire the deprecation warning on attribute access.

    A plain ``import hh_applicant_tool.api`` only executes this
    module's body, which has no top-level ``warnings.warn`` call --
    so the package import is silent.  The warning fires the first
    time a caller actually touches an attribute (submodule or
    symbol), which is when the deprecation is meaningful.

    CPython's import machinery calls :pep:`562` ``__getattr__`` *twice*
    for the ``from X import Y`` form: once via :func:`getattr` to fetch
    the value, and a second time via :func:`hasattr` from
    :func:`importlib._handle_fromlist` (only when ``Y`` is in
    ``__all__``).  We therefore cache the resolved attribute in this
    module's ``__dict__`` so the second lookup short-circuits and the
    warning is not emitted twice for a single import statement.
    """
    if name in _LAZY_SUBMODULES:
        warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
        submodule = importlib.import_module(_LAZY_SUBMODULES[name])
        # Cache in sys.modules (so the standard ``import
        # hh_applicant_tool.api.datatypes`` path keeps working) and in
        # this module's __dict__ (so a subsequent ``getattr`` /
        # ``hasattr`` from ``_handle_fromlist`` finds the attribute
        # without re-invoking ``__getattr__`` and re-warning).
        sys.modules.setdefault(_LAZY_SUBMODULES[name], submodule)
        sys.modules[__name__].__dict__[name] = submodule
        return submodule
    if name in _RAW_SYMBOLS:
        mod_name, _, attr_name = _RAW_SYMBOLS[name].rpartition(".")
        mod = importlib.import_module(mod_name)
        warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
        value = getattr(mod, attr_name)
        sys.modules[__name__].__dict__[name] = value
        return value
    raise AttributeError(
        f"module 'hh_applicant_tool.api' has no attribute {name!r}"
    )


__all__ = sorted(_LAZY_SUBMODULES) + sorted(_RAW_SYMBOLS)
