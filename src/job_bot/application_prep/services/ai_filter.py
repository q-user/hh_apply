"""``AiFilterService`` — per-profile AI filter client construction (issue #147).

VSA replacement for the legacy
``PrepareVacanciesUseCase._init_ai_filter`` + the
``vacancy_filter_ai_factory`` call site. The service is pure (no DB)
and stateless: every :meth:`build` call uses the caller's
``relevance_obj`` and ``factory`` so the same instance can be reused
across multiple profiles in a single run.

The service is a thin VSA wrapper around the existing
:func:`job_bot.application_prep.utils.build_filter_ai_client`
helper. The helper is duck-typed on ``relevance_obj.ai_client`` so it
works with both the VSA
:class:`job_bot.application_prep.handlers.relevance_handler.RelevanceHandler`
and the legacy ``hh_applicant_tool.services.relevance.RelevanceService``
(both expose ``ai_client`` as a property/setter and
``analyze_resume_heavy`` / ``analyze_resume_light`` methods).

Why a service at all? The four services form a uniform
``services/`` package so the orchestrator's wiring is symmetric. A
1-line method that delegates to a free function is the price of that
uniformity, and it makes the architecture's intent obvious in the
``__init__`` of any consumer.
"""

from __future__ import annotations

from typing import Any, Callable

from job_bot.application_prep.utils import build_filter_ai_client


class AiFilterService:
    """Build the per-profile AI filter client and inject it into the
    relevance object.

    Pure (no DB). Stateless. Reusable across profiles in a run.

    Example:
        >>> service = AiFilterService()
        >>> ai_client = service.build(
        ...     profile=profile,
        ...     resume={"id": "r1"},
        ...     relevance_obj=relevance_handler,
        ...     factory=my_factory,
        ...     rate_limit=40,
        ... )
    """

    def build(
        self,
        *,
        profile: Any,
        resume: dict[str, Any],
        relevance_obj: Any,
        factory: Callable[[str], Any] | None,
        rate_limit: Any = None,
    ) -> Any:
        """Build the per-profile AI client and inject it via
        ``relevance_obj.ai_client``.

        Args:
            profile: search profile (duck-typed; reads ``ai_filter_mode``
                and ``relevance_rules``).
            resume: resume dict passed to ``analyze_resume_*``.
            relevance_obj: object receiving the ``ai_client`` assignment
                (typically a VSA ``RelevanceHandler`` or a legacy
                ``RelevanceService``).
            factory: ``vacancy_filter_ai_factory(system_prompt) -> AI
                client`` callable, or ``None`` if no factory was
                provided.
            rate_limit: optional rate limit assigned to the produced
                AI client (best-effort; failure is logged at DEBUG).

        Returns:
            The AI client produced by ``factory``, or ``None`` if no
            filter is needed / available / the factory raised. The AI
            client is also assigned to ``relevance_obj.ai_client`` on
            the success path; on early-exit / failure paths
            ``relevance_obj.ai_client`` is reset to ``None``.
        """
        return build_filter_ai_client(
            profile=profile,
            resume=resume,
            relevance_obj=relevance_obj,
            factory=factory,
            rate_limit=rate_limit,
        )
