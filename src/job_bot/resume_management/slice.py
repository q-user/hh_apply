"""Resume Management slice - create & clone resumes (issue #137).

Migrated from ``hh_applicant_tool.operations.create_resume`` and
``hh_applicant_tool.operations.clone_resume``. The slice is
self-contained — its only collaborator is an
:class:`HhApiClientPort`. There is no DB dependency because the
legacy operations read the resume list directly from the HH API.
"""

from __future__ import annotations

from job_bot.resume_management.handlers.resume_clone_handler import (
    ResumeCloneHandler,
)
from job_bot.resume_management.handlers.resume_create_handler import (
    FileSystemTemplateLoader,
    ResumeCreateHandler,
)
from job_bot.resume_management.ports.api_client_port import HhApiClientPort
from job_bot.resume_management.ports.template_loader_port import (
    TemplateLoaderPort,
)


class ResumeManagementSlice:
    """Vertical slice for resume management.

    Attributes:
        api_client: The HH API client the slice operates on.
        create_resume: The :class:`ResumeCreateHandler` port.
        clone_resume: The :class:`ResumeCloneHandler` port.
    """

    def __init__(
        self,
        api_client: HhApiClientPort,
        template_loader: TemplateLoaderPort | None = None,
    ) -> None:
        self._api_client = api_client
        self._create_resume = ResumeCreateHandler(
            api_client=api_client,
            template_loader=template_loader or FileSystemTemplateLoader(),
        )
        self._clone_resume = ResumeCloneHandler(api_client=api_client)

    @property
    def api_client(self) -> HhApiClientPort:
        """Return the HH API client the slice uses."""
        return self._api_client

    @property
    def create_resume(self) -> ResumeCreateHandler:
        """Return the create-resume port."""
        return self._create_resume

    @property
    def clone_resume(self) -> ResumeCloneHandler:
        """Return the clone-resume port."""
        return self._clone_resume


def create_resume_management_slice(
    api_client: HhApiClientPort,
    template_loader: TemplateLoaderPort | None = None,
) -> ResumeManagementSlice:
    """Factory function to create a :class:`ResumeManagementSlice`.

    Args:
        api_client: HH API client to use. Required.
        template_loader: Optional :class:`TemplateLoaderPort`; defaults
            to :class:`FileSystemTemplateLoader`.

    Returns:
        Configured :class:`ResumeManagementSlice`.
    """
    return ResumeManagementSlice(
        api_client=api_client,
        template_loader=template_loader,
    )


__all__ = [
    "ResumeManagementSlice",
    "create_resume_management_slice",
]
