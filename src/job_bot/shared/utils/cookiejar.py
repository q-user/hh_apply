"""Cookie storage helpers used across VSA slices (issue #151).

Mirrors the legacy :mod:`hh_applicant_tool.utils.cookiejar` module but
exposes a strict, type-clean API. The :class:`HHOnlyCookieJar` keeps
cookies only for the ``hh.*`` domain family (``hh.ru``, ``hh.kz``,
``hh.uz``, ``hh.by``, ``hh.net``, ``hh.com``) and silently drops
everything else.
"""

from __future__ import annotations

import re
from http.cookiejar import Cookie, MozillaCookieJar

__all__ = ["HHOnlyCookieJar"]


# Regular expression matching the canonical ``hh.*`` domain family. The
# leading alternation accepts both ``hh.ru`` and ``.hh.ru`` (the
# leading-dot form is what stdlib ``Cookie`` instances usually carry).
_HH_DOMAIN_RE = re.compile(r"(\.|^)hh\.(ru|kz|uz|by|net|com)[.]?$")


class HHOnlyCookieJar(MozillaCookieJar):
    """A :class:`MozillaCookieJar` that only keeps cookies for ``hh.*`` domains.

    Any cookie whose domain does not match the canonical ``hh.``
    domain family is silently dropped on :meth:`set_cookie`. This
    keeps the session cookie store from leaking third-party cookies
    when the underlying HTTP library transparently follows cross-site
    redirects.
    """

    def set_cookie(self, cookie: Cookie) -> None:
        """Add *cookie* to the jar only when its domain matches ``hh.*``."""
        if _HH_DOMAIN_RE.search(cookie.domain):
            super().set_cookie(cookie)
