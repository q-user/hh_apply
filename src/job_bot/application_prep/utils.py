"""Application Preparation utilities (issue #54).

Shared helpers used by both the new VSA slice and the legacy
``hh_applicant_tool.services.*`` shims. Keeping these in one place
prevents the three identical copies that used to exist
(``services/applications.py``, ``services/relevance.py``,
``container.py``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from hh_applicant_tool.ai.base import AIError
from job_bot.application_prep.handlers.relevance_handler import (
    build_filter_system_prompt_heavy,
    build_filter_system_prompt_light,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def analysis_to_dict(result: Any) -> dict[str, Any]:
    """Convert a ``RelevanceResult`` (new slice model or legacy) to a dict
    for ``application_drafts.analysis_json``.

    Duck-typed on purpose: accepts any object with ``suitable`` / ``score`` /
    ``reason`` / ``raw_response`` attributes, regardless of which model
    class produced it. ``None`` fields are dropped to avoid bloating JSON.
    """
    out: dict[str, Any] = {"suitable": bool(getattr(result, "suitable", False))}
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


def build_filter_ai_client(
    *,
    profile: Any,
    resume: dict[str, Any],
    relevance_obj: Any,
    factory: Callable[[str], Any] | None,
    rate_limit: Any = None,
) -> Any:
    """Build the per-profile filter AI client and inject it via the
    ``ai_client`` setter on ``relevance_obj``.

    Shared helper for the VSA
    :class:`job_bot.application_prep.handlers.relevance_handler.RelevanceHandler`
    (the single source of truth — issue #135 removed the legacy
    ``hh_applicant_tool.services.relevance.RelevanceService`` path from
    ``ApplicationPrepSlice``). Both VSA and the legacy shim expose the
    same ``ai_client`` property/setter contract, plus
    ``analyze_resume_heavy`` / ``analyze_resume_light`` methods.

    Args:
        profile: search profile (duck-typed; reads ``ai_filter_mode`` and
            ``relevance_rules``).
        resume: resume dict passed to ``analyze_resume_*``.
        relevance_obj: the object to receive the ``ai_client`` assignment.
            ``relevance_obj.ai_client = None`` is always set on early-exit
            paths so callers can rely on a known-clear state.
        factory: ``vacancy_filter_ai_factory(system_prompt) -> AI client``
            callable, or ``None`` if no factory was provided.
        rate_limit: optional rate limit assigned to the produced AI
            client (best-effort; failure is logged at DEBUG).

    Returns:
        The AI client produced by ``factory``, or ``None`` if no filter
        is needed / available / the factory raised. The AI client is
        also assigned to ``relevance_obj.ai_client`` on the success
        path; on early-exit / failure paths ``relevance_obj.ai_client``
        is reset to ``None``.
    """
    mode = getattr(profile, "ai_filter_mode", None)
    relevance_rules = getattr(profile, "relevance_rules", None)

    if not mode:
        relevance_obj.ai_client = None
        return None
    if mode not in ("heavy", "light"):
        logger.warning(
            "Неизвестный ai_filter_mode=%r для профиля %s — AI-фильтр пропущен",
            mode,
            getattr(profile, "id", "?"),
        )
        relevance_obj.ai_client = None
        return None
    if factory is None:
        logger.warning(
            "ai_filter_mode=%r, но vacancy_filter_ai_factory не задан",
            mode,
        )
        relevance_obj.ai_client = None
        return None

    if mode == "heavy":
        resume_analysis = relevance_obj.analyze_resume_heavy(resume)
        system_prompt = build_filter_system_prompt_heavy(
            resume_analysis, relevance_rules=relevance_rules
        )
    else:  # light
        resume_analysis = relevance_obj.analyze_resume_light(resume)
        system_prompt = build_filter_system_prompt_light(
            resume_analysis, relevance_rules=relevance_rules
        )

    try:
        ai_client = factory(system_prompt)
    except (ValueError, TypeError, AIError, RuntimeError) as ex:
        logger.warning("Не удалось создать AI-клиент фильтра: %s", ex)
        relevance_obj.ai_client = None
        return None
    except Exception as ex:  # noqa: BLE001
        logger.warning(
            "Неожиданная ошибка при создании AI-клиента фильтра: %s",
            ex,
        )
        relevance_obj.ai_client = None
        return None

    if rate_limit is not None:
        try:
            ai_client.rate_limit = rate_limit
        except Exception as ex:  # noqa: BLE001
            logger.debug("rate_limit assignment failed: %s", ex)

    relevance_obj.ai_client = ai_client
    return ai_client
