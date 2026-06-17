"""Resume management services (issue #151).

Each service is a thin VSA wrapper around a phase of the resume
creation / clone pipeline. The :class:`ResumeRenderer` service
converts a markdown resume template into the dict payload expected
by ``POST /resumes``.
"""

from job_bot.resume_management.services.resume_renderer import (
    ResumeRenderer,
    parse_resume_md,
)

__all__ = ["ResumeRenderer", "parse_resume_md"]
