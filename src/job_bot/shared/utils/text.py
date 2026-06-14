"""String/HTML helpers used across VSA slices.

Mirrors the legacy :mod:`hh_applicant_tool.utils.string` module but
exposes a strict, type-clean API. New code should import from this
module directly; the legacy location is a deprecation shim.
"""

from __future__ import annotations

import random
import re
from typing import Any


def shorten(s: str, limit: int = 75, ellipsis: str = "…") -> str:
    """Truncate ``s`` to ``limit`` chars and append ``ellipsis`` if cut."""
    return s[:limit] + bool(s[limit:]) * ellipsis


def rand_text(s: str) -> str:
    """Resolve ``{opt1|opt2}`` placeholders by picking one alternative.

    Walks the template repeatedly so nested placeholders are expanded
    (innermost first). Pure random — useful for templated cover letters.
    """
    while (
        temp := re.sub(
            r"{([^{}]+)}",
            lambda m: random.choice(
                m.group(1).split("|"),
            ),
            s,
        )
    ) != s:
        s = temp
    return s


def bool2str(v: bool) -> str:
    """Lowercase ``"true"`` / ``"false"`` for HH.ru query params."""
    return str(v).lower()


# К удалению
def list2str(items: list[Any] | None) -> str:
    """Comma-join ``items`` as ``str(v)``, returning ``""`` for None/empty."""
    return ",".join(f"{v}" for v in items) if items else ""


def unescape_string(text: str) -> str:
    """Reverse the common ``\\\\n`` / ``\\\\r`` / ``\\\\t`` / ``\\\\\\\\`` escapes."""
    if not text:
        return ""
    return (
        text.replace(r"\n", "\n")
        .replace(r"\r", "\r")
        .replace(r"\t", "\t")
        .replace(r"\\", "\\")
    )


def br2nl(s: str) -> str:
    """Convert ``<br>`` / ``<br/>`` tags to newlines (case-insensitive)."""
    return re.sub(r"<br\s*/?>", "\n", s, flags=re.I)


def strip_tags(content: str) -> str:
    """Strip HTML tags from ``content`` and normalise ``<br>`` to newlines."""
    content = br2nl(content)
    content = re.sub(r"<[^>]+>", "", content)
    # content = re.sub(r"\s+", " ", content)
    return content.strip()


__all__ = [
    "bool2str",
    "br2nl",
    "list2str",
    "rand_text",
    "shorten",
    "strip_tags",
    "unescape_string",
]
