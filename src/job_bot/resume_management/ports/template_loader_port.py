"""Template loader port for the resume_management slice (issue #137).

The legacy ``create_resume`` operation read a ``.md`` or ``.toml``
file from disk and parsed it inline. The VSA slice abstracts that
behind :class:`TemplateLoaderPort` so:

* tests can verify the parse step in isolation,
* future code paths (HTTP-loaded templates, in-process fixtures) can
  be plugged in without touching the handler.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TemplateLoaderPort(Protocol):
    """Load a resume template and return its parsed representation."""

    def load(self, path: Path) -> dict[str, Any]:
        """Return the parsed template as a plain dict.

        The default :class:`FileSystemTemplateLoader` reads ``.md``
        through ``hh_applicant_tool.utils.resume_md.parse_resume_md``
        and ``.toml`` through ``tomllib``.
        """
        ...


__all__ = ["TemplateLoaderPort"]
