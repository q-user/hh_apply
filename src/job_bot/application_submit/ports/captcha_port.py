"""CaptchaPort -- interface for CAPTCHA solving.

Implemented by :class:`job_bot.application_submit.handlers.captcha_handler.CaptchaHandler`.
The handler wraps the legacy ``_solve_captcha_async`` helper extracted
from ``ApplyToVacanciesUseCase`` (issue #145) and exposes a sync
wrapper suitable for the sync apply pipeline.
"""

from __future__ import annotations

from typing import Any, Protocol


class CaptchaPort(Protocol):
    """CAPTCHA solving (uses ``CaptchaSolverPort`` or legacy Playwright)."""

    def solve_captcha(self, captcha_url: str) -> bool:
        """Sync wrapper over :meth:`solve_captcha_async` (the apply
        pipeline is sync; the underlying solver may be async).
        """
        ...

    async def solve_captcha_async(self, captcha_url: str) -> bool:
        """Solve a CAPTCHA and return ``True`` on success.

        Prefers the ``CaptchaSolverPort`` when supplied; falls back to
        the legacy Playwright path.
        """
        ...


__all__ = ["CaptchaPort"]
