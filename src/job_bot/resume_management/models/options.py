"""Domain models for the resume_management slice (issue #137).

These are the in/out DTOs of the two handlers — they keep the
``run`` methods small and let tests assert on the exact shape of the
result.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CreateOptions:
    """Inputs to :meth:`ResumeCreateHandler.create`.

    Attributes:
        template: Path to a ``.md`` or ``.toml`` resume template.
        dry_run: If ``True``, the handler prints the resolved payload
            and skips the API call. Mirrors ``--dry-run`` on the
            legacy CLI.
        publish: If ``True``, the handler follows the ``POST /resumes``
            call with ``POST /resumes/{id}/publish``. Mirrors
            ``--publish``.
    """

    template: Path
    dry_run: bool = False
    publish: bool = False


@dataclass
class CreateResult:
    """Outcome of a single :meth:`ResumeCreateHandler.create` call."""

    ok: bool = True
    error: str | None = None
    resume_id: str | None = None
    dry_run_payload: dict[str, Any] | None = None
    published: bool = False


@dataclass
class CloneResult:
    """Outcome of a single :meth:`ResumeCloneHandler.clone` call."""

    ok: bool = True
    error: str | None = None
    cloned_resume_id: str | None = None


__all__ = [
    "CloneResult",
    "CreateOptions",
    "CreateResult",
]
