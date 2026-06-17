"""SubmitResult DTO returned by the worker service."""

from __future__ import annotations

from dataclasses import dataclass


class SubmitStatus:
    """Submit result status -- mirrors :class:`ApplyJobStatus` outcomes."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"  # retry scheduled


@dataclass
class SubmitResult:
    """Result of one worker tick."""

    status: str
    job_id: int
    draft_id: int
    attempts: int
    last_error: str | None = None

    @property
    def succeeded(self) -> bool:
        """``True`` when the apply was applied successfully."""
        return self.status == SubmitStatus.SUCCEEDED

    @property
    def failed(self) -> bool:
        """``True`` when the apply was marked as failed (no retry)."""
        return self.status == SubmitStatus.FAILED

    @property
    def skipped(self) -> bool:
        """``True`` when a retry is scheduled (transient error)."""
        return self.status == SubmitStatus.SKIPPED


__all__ = ["SubmitResult", "SubmitStatus"]
