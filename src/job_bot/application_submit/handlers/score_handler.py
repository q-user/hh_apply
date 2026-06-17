"""ScoreHandler -- AI relevance filtering (per-resume init + per-vacancy).

In-slice VSA wrapper (issue #145) for the legacy
``ApplyToVacanciesUseCase._init_ai_filter`` helper. Delegates the
actual AI calls to
:class:`job_bot.application_prep.handlers.relevance_handler.RelevanceHandler`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, cast

from job_bot.application_prep.utils import (  # type: ignore[attr-defined]
    build_filter_system_prompt_heavy,
    build_filter_system_prompt_light,
)

if TYPE_CHECKING:
    from job_bot.application_prep.handlers.relevance_handler import (
        RelevanceHandler,
    )

logger = logging.getLogger(__package__)


class ScoreHandler:
    """In-slice score handler (issue #145).

    Args:
        relevance_handler: the underlying VSA
            :class:`RelevanceHandler` (the single source of truth for
            AI relevance analysis).
        vacancy_filter_ai: pre-injected AI client for the per-resume
            filter, or ``None`` to use ``vacancy_filter_ai_factory``.
        vacancy_filter_ai_factory: factory ``(system_prompt) -> AI client``
            used when ``command.ai_filter`` is set; the produced client
            is also assigned to ``relevance_handler.ai_client`` so the
            downstream ``is_suitable_*`` calls use it.
    """

    def __init__(
        self,
        relevance_handler: "RelevanceHandler",
        *,
        vacancy_filter_ai: Any = None,
        vacancy_filter_ai_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._relevance = relevance_handler
        self._vacancy_filter_ai = vacancy_filter_ai
        self._vacancy_filter_ai_factory = vacancy_filter_ai_factory

    @property
    def relevance_handler(self) -> "RelevanceHandler":
        """The underlying :class:`RelevanceHandler` instance."""
        return self._relevance

    @property
    def vacancy_filter_ai(self) -> Any:
        """The currently active per-resume AI client (``None`` disables
        filtering). May be ``None`` even after :meth:`init_ai_filter`
        if no factory was provided.
        """
        return self._vacancy_filter_ai

    def init_ai_filter(self, resume: dict[str, Any], command: Any) -> str:
        """Initialize the AI filter for ``resume`` (issue #145).

        Builds the system prompt (heavy or light) via the underlying
        :class:`RelevanceHandler`, creates the per-resume AI client
        via the factory, and assigns it to the relevance handler so
        downstream :meth:`is_suitable` calls use it.

        Returns the ``resume_analysis`` text (used by the cover-letter
        handler). When ``command.ai_filter`` is falsy, returns
        ``""`` and leaves the relevance handler's AI client unset.
        """
        ai_filter = command.ai_filter
        if not ai_filter:
            return ""

        relevance_handler = self._relevance
        if ai_filter == "heavy":
            resume_analysis = relevance_handler.analyze_resume_heavy(resume)
            system_prompt = build_filter_system_prompt_heavy(
                resume_analysis,
                relevance_rules=relevance_handler._relevance_rules,
            )
        elif ai_filter == "light":
            resume_analysis = relevance_handler.analyze_resume_light(resume)
            system_prompt = build_filter_system_prompt_light(
                resume_analysis,
                relevance_rules=relevance_handler._relevance_rules,
            )
        else:
            raise ValueError(f"Неизвестный режим AI фильтра: {ai_filter}")

        logger.debug("AI системный промпт (%s): %s", ai_filter, system_prompt)

        if self._vacancy_filter_ai_factory is not None:
            self._vacancy_filter_ai = self._vacancy_filter_ai_factory(
                system_prompt
            )
        elif self._vacancy_filter_ai is None:
            raise ValueError(
                "AI фильтр включён, но ни vacancy_filter_ai, "
                "ни vacancy_filter_ai_factory не заданы"
            )

        if command.ai_rate_limit and self._vacancy_filter_ai is not None:
            self._vacancy_filter_ai.rate_limit = command.ai_rate_limit
        relevance_handler.ai_client = self._vacancy_filter_ai
        return resume_analysis

    def is_suitable(self, vacancy: dict[str, Any], command: Any) -> bool:
        """Check if ``vacancy`` is suitable per the AI filter.

        When ``command.ai_filter`` is falsy, returns ``True`` (no
        filtering requested). Otherwise delegates to
        :meth:`RelevanceHandler.is_suitable_heavy` or
        :meth:`RelevanceHandler.is_suitable_light` based on the mode.
        """
        ai_filter = command.ai_filter
        if not ai_filter:
            return True
        if ai_filter == "heavy":
            return relevance_handler_is_suitable_heavy(self._relevance, vacancy)
        if ai_filter == "light":
            return relevance_handler_is_suitable_light(self._relevance, vacancy)
        raise ValueError(f"Неизвестный режим AI фильтра: {ai_filter}")


def relevance_handler_is_suitable_heavy(
    relevance_handler: Any, vacancy: dict[str, Any]
) -> bool:
    """Wrap ``relevance_handler.is_suitable_heavy`` and return ``.suitable``."""
    result = relevance_handler.is_suitable_heavy(cast(Any, vacancy))
    return bool(result.suitable)


def relevance_handler_is_suitable_light(
    relevance_handler: Any, vacancy: dict[str, Any]
) -> bool:
    """Wrap ``relevance_handler.is_suitable_light`` and return ``.suitable``."""
    result = relevance_handler.is_suitable_light(cast(Any, vacancy))
    return bool(result.suitable)


__all__ = ["ScoreHandler"]
