"""Submit-phase CoverLetterHandler -- thin adapter over prep's handler.

The prep and submit phases share the underlying
:class:`job_bot.application_prep.handlers.cover_letter_handler.CoverLetterHandler`
instance. This in-slice wrapper exposes the submit-phase contract
(``generate(...)`` with ``force`` and ``required_by_vacancy`` flags)
without leaking the prep phase's persistence methods.

Issue #145: extracted from
``ApplyToVacanciesUseCase._generate_cover_letter``.
"""

from __future__ import annotations

import logging
from typing import Any, cast

logger = logging.getLogger(__package__)


class CoverLetterHandler:
    """Submit-phase cover-letter handler (issue #145).

    Args:
        cover_letter_handler: the underlying prep-phase handler. Must
            expose ``generate_cover_letter(vacancy, placeholders, *,
            resume_analysis, resume, force, required_by_vacancy)``.
    """

    def __init__(self, cover_letter_handler: Any) -> None:
        self._handler = cover_letter_handler

    def generate(
        self,
        vacancy: dict[str, Any],
        placeholders: dict[str, Any],
        *,
        resume_analysis: str = "",
        resume: dict[str, Any] | None = None,
        force: bool = False,
        required_by_vacancy: bool = False,
    ) -> str:
        """Generate a cover letter for ``vacancy`` in the submit phase.

        Thin pass-through to the prep-phase handler's
        :meth:`generate_cover_letter`. The submit phase does not
        persist the letter (it goes straight into the
        ``/negotiations`` POST body).
        """
        return cast(
            str,
            self._handler.generate_cover_letter(
                vacancy,
                placeholders,
                resume_analysis=resume_analysis,
                resume=resume,
                force=force,
                required_by_vacancy=required_by_vacancy,
            ),
        )


__all__ = ["CoverLetterHandler"]
