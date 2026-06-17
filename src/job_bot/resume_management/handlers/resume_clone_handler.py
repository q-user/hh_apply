"""Resume clone handler for the resume_management slice (issue #137).

Migrated from ``hh_applicant_tool.operations.clone_resume``. The
handler:

1. lists the user's existing resumes via ``GET /resumes/mine``,
2. picks the requested resume (or the first one),
3. POSTs the ``/resume_profile`` payload that hh.ru expects for a
   clone.

The wire-level payload is preserved verbatim from the legacy code so
the CLI shim's behaviour is identical.
"""

from __future__ import annotations

import logging
from typing import Any

from job_bot.shared.api.errors import ApiError

from job_bot.resume_management.models.options import CloneResult
from job_bot.resume_management.ports.api_client_port import HhApiClientPort

logger = logging.getLogger(__name__)

# Endpoint that clones an existing resume. Mirrors the legacy hard-coded
# value in ``hh_applicant_tool.operations.clone_resume``.
_CLONE_ENDPOINT = "/resume_profile"

# Payload flag that hh.ru reads as "this clone can be used to apply to
# any job" — preserved from the legacy implementation.
_CLONE_ANY_JOB = True


class ResumeCloneHandler:
    """Clone an existing resume via ``POST /resume_profile``."""

    def __init__(self, api_client: HhApiClientPort) -> None:
        self.api_client = api_client

    def clone(self, resume_id: str | None = None) -> CloneResult:
        """Run the clone flow.

        Args:
            resume_id: ID of the resume to clone. When ``None``, the
                first resume returned by ``GET /resumes/mine`` is used
                (matching the legacy default).

        Returns:
            :class:`CloneResult` describing the outcome.
        """
        try:
            resumes: list[dict[str, Any]] = self.api_client.get(
                "/resumes/mine"
            ).get("items", [])
        except ApiError as ex:
            logger.error("Не удалось получить список резюме: %s", ex)
            return CloneResult(ok=False, error=str(ex))

        if not resumes:
            return CloneResult(ok=False, error="no resumes available to clone")

        if resume_id is None:
            target = resumes[0]
        else:
            by_id = {r["id"]: r for r in resumes}
            if resume_id not in by_id:
                return CloneResult(
                    ok=False,
                    error=f"resume_id {resume_id!r} not found in /resumes/mine",
                )
            target = by_id[resume_id]

        payload = {
            "additional_properties": {"any_job": _CLONE_ANY_JOB},
            "clone_resume_id": target["id"],
        }

        try:
            result = self.api_client.post(
                _CLONE_ENDPOINT, payload, as_json=True
            )
            logger.debug("POST /resume_profile response: %s", result)
        except ApiError as ex:
            logger.error("Произошла ошибка при клонировании резюме: %s", ex)
            return CloneResult(ok=False, error=str(ex))

        return CloneResult(ok=True, cloned_resume_id=result.get("id"))


__all__ = ["ResumeCloneHandler"]
