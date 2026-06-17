"""Tests for :mod:`job_bot.shared.utils.cookiejar` (issue #151).

The VSA port of the legacy :mod:`hh_applicant_tool.utils.cookiejar`
module preserves the public :class:`HHOnlyCookieJar` name and
behaviour: a :class:`http.cookiejar.MozillaCookieJar` subclass that
silently drops cookies whose domain is **not** an ``hh.*`` domain
(``hh.ru``, ``hh.kz``, ``hh.uz``, ``hh.by``, ``hh.net``, ``hh.com``).

Tests use the stdlib :class:`http.cookiejar.Cookie` directly so we
exercise the actual filter, not a stub.
"""

from __future__ import annotations

from http.cookiejar import Cookie

import pytest

from job_bot.shared.utils.cookiejar import HHOnlyCookieJar


def _make_cookie(domain: str, name: str = "session") -> Cookie:
    """Build a stdlib :class:`Cookie` with the given domain.

    ``domain`` must include the leading dot for non-``hh`` cookies
    (e.g. ``.google.com``); the production code uses it as-is. The
    remaining required fields are populated with stable, harmless
    placeholders.
    """
    return Cookie(
        version=0,
        name=name,
        value="v",
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


@pytest.mark.parametrize(
    "domain",
    [
        "hh.ru",
        ".hh.ru",
        "hh.kz",
        "hh.uz",
        "hh.by",
        "hh.net",
        "hh.com",
    ],
)
def test_hh_domains_are_kept(domain: str) -> None:
    """All canonical ``hh.*`` domains pass the filter."""
    jar = HHOnlyCookieJar()
    cookie = _make_cookie(domain)
    jar.set_cookie(cookie)
    assert len(jar) == 1
    assert list(jar)[0].domain.lstrip(".") == domain.lstrip(".")


@pytest.mark.parametrize(
    "domain",
    [
        "google.com",
        ".google.com",
        "example.org",
        "evil-hh.ru",  # subdomain suffix attack
        "hhru",  # missing TLD
        "hhx.ru",  # different TLD
        "127.0.0.1",
    ],
)
def test_non_hh_domains_are_dropped(domain: str) -> None:
    """Cookies from non-``hh`` domains are silently dropped."""
    jar = HHOnlyCookieJar()
    jar.set_cookie(_make_cookie(domain))
    assert len(jar) == 0


def test_mixed_cookies_filter_correctly() -> None:
    """A mixed set of cookies keeps only the ``hh.*`` ones."""
    jar = HHOnlyCookieJar()
    jar.set_cookie(_make_cookie("hh.ru", name="keep1"))
    jar.set_cookie(_make_cookie("google.com", name="drop1"))
    jar.set_cookie(_make_cookie(".hh.kz", name="keep2"))
    jar.set_cookie(_make_cookie("example.org", name="drop2"))

    kept = {c.name for c in jar}
    assert kept == {"keep1", "keep2"}


def test_subclass_of_mozilla_cookie_jar() -> None:
    """``HHOnlyCookieJar`` is a :class:`MozillaCookieJar` subclass."""
    from http.cookiejar import MozillaCookieJar

    assert issubclass(HHOnlyCookieJar, MozillaCookieJar)
