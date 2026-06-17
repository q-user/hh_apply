"""Resume Management slice - create & clone resumes (issue #137).

Migrated from ``hh_applicant_tool.operations.create_resume`` and
``hh_applicant_tool.operations.clone_resume``. The slice is
self-contained — its only collaborator is an ``HhApiClientPort``.

Public surface:

* :class:`ResumeManagementSlice` — slice container.
* :func:`create_resume_management_slice` — factory.
* :class:`ResumeCreateHandler` / :class:`ResumeCloneHandler` —
  the underlying handlers.
* :class:`HhApiClientPort` / :class:`TemplateLoaderPort` — ports.
* :class:`CreateOptions` / :class:`CreateResult` / :class:`CloneResult`
  — DTOs.
* :class:`ResumeRenderer` / :func:`parse_resume_md` — markdown
  resume template renderer (issue #151).
"""

from job_bot.resume_management.handlers.resume_clone_handler import (
    ResumeCloneHandler,
)
from job_bot.resume_management.handlers.resume_create_handler import (
    FileSystemTemplateLoader,
    InMemoryTemplateLoader,
    ResumeCreateHandler,
)
from job_bot.resume_management.models.options import (
    CloneResult,
    CreateOptions,
    CreateResult,
)
from job_bot.resume_management.ports.api_client_port import HhApiClientPort
from job_bot.resume_management.ports.template_loader_port import (
    TemplateLoaderPort,
)
from job_bot.resume_management.services.resume_renderer import (
    ResumeRenderer,
    parse_resume_md,
)
from job_bot.resume_management.slice import (
    ResumeManagementSlice,
    create_resume_management_slice,
)

__all__ = [
    "CloneResult",
    "CreateOptions",
    "CreateResult",
    "FileSystemTemplateLoader",
    "HhApiClientPort",
    "InMemoryTemplateLoader",
    "ResumeCloneHandler",
    "ResumeCreateHandler",
    "ResumeManagementSlice",
    "ResumeRenderer",
    "TemplateLoaderPort",
    "create_resume_management_slice",
    "parse_resume_md",
]
