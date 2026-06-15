"""Ports for the resume_management slice (issue #137)."""

from job_bot.resume_management.ports.api_client_port import HhApiClientPort
from job_bot.resume_management.ports.template_loader_port import (
    TemplateLoaderPort,
)

__all__ = ["HhApiClientPort", "TemplateLoaderPort"]
