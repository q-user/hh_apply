"""Application Preparation utilities (issue #54).

Shared helpers used by both the new VSA slice and the legacy
``hh_applicant_tool.services.*`` shims. Keeping these in one place
prevents the three identical copies that used to exist
(``services/applications.py``, ``services/relevance.py``,
``container.py``).
"""

from __future__ import annotations

from typing import Any


def analysis_to_dict(result: Any) -> dict:
    """Convert a ``RelevanceResult`` (new slice model or legacy) to a dict
    for ``application_drafts.analysis_json``.

    Duck-typed on purpose: accepts any object with ``suitable`` / ``score`` /
    ``reason`` / ``raw_response`` attributes, regardless of which model
    class produced it. ``None`` fields are dropped to avoid bloating JSON.
    """
    out: dict = {"suitable": bool(getattr(result, "suitable", False))}
    score = getattr(result, "score", None)
    if score is not None:
        out["score"] = score
    reason = getattr(result, "reason", None)
    if reason is not None:
        out["reason"] = reason
    raw = getattr(result, "raw_response", None)
    if raw is not None:
        out["raw_response"] = raw
    return out
