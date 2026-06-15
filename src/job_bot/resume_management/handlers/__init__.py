"""Handlers for the resume_management slice (issue #137)."""

from job_bot.resume_management.handlers.resume_clone_handler import (
    ResumeCloneHandler,
)
from job_bot.resume_management.handlers.resume_create_handler import (
    FileSystemTemplateLoader,
    InMemoryTemplateLoader,
    ResumeCreateHandler,
)

__all__ = [
    "FileSystemTemplateLoader",
    "InMemoryTemplateLoader",
    "ResumeCloneHandler",
    "ResumeCreateHandler",
]
