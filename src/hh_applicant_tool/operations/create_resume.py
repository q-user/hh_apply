"""Create a resume on hh.ru from a ``.md`` or ``.toml`` template.

.. deprecated:: 1.9
   Use :class:`job_bot.resume_management.ResumeManagementSlice` (or
   :func:`job_bot.resume_management.create_resume_management_slice`)
   instead. This module is part of the VSA switchover (issue #137)
   and **planned for removal in version 2.0**.

Legacy module that powered the ``create-resume`` CLI command.
The body has been migrated to
:mod:`job_bot.resume_management`; this file is kept as a thin shim
that delegates to the VSA slice and emits a
:class:`DeprecationWarning` on instantiation.

Public surface (CLI flags, namespace, aliases) is preserved verbatim
so the existing ``hh-applicant-tool create-resume …`` command
continues to work.
"""

from __future__ import annotations

import argparse
import logging
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

# Issue #137: this module is deprecated. The deprecation warning
# fires on instantiation of ``Operation`` (not at import time) so
# that just importing the module for argparse dispatch does not
# pollute every test run.

logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    """Backwards-compat namespace — the VSA slice does not own CLI args."""

    template: Path
    dry_run: bool
    publish: bool


class Operation(BaseOperation):
    """Thin shim that delegates to :class:`job_bot.resume_management.ResumeCreateHandler`.

    Public surface preserved verbatim from the legacy implementation:

    * ``__aliases__`` — ``["create-resume"]``.
    * ``setup_parser`` — ``template``, ``--dry-run``, ``--publish``.
    * ``run`` — dispatch to the VSA handler.
    """

    __aliases__ = ["create-resume"]

    def __init__(self) -> None:
        warnings.warn(
            "hh_applicant_tool.operations.create_resume is deprecated; "
            "use job_bot.resume_management instead (issue #137).",
            DeprecationWarning,
            stacklevel=2,
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "template",
            type=Path,
            help="Путь до шаблона резюме (.md или .toml)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать итоговый payload без отправки запроса",
        )
        parser.add_argument(
            "--publish",
            action="store_true",
            help="Опубликовать резюме сразу после создания",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> int | None:
        """Dispatch the legacy ``create-resume`` command to the VSA handler."""
        from job_bot.resume_management import create_resume_management_slice

        slice_ = create_resume_management_slice(api_client=tool.api_client)
        result = slice_.create_resume.create(
            template=args.template,
            dry_run=args.dry_run,
            publish=args.publish,
        )
        if not result.ok:
            return 1
        return None
