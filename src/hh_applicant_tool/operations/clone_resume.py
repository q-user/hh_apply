"""Clone an existing hh.ru resume via ``POST /resume_profile``.

.. deprecated:: 1.9
   Use :class:`job_bot.resume_management.ResumeManagementSlice` (or
   :func:`job_bot.resume_management.create_resume_management_slice`)
   instead. This module is part of the VSA switchover (issue #137)
   and **planned for removal in version 2.0**.

Legacy module that powered the ``clone-resume`` CLI command.
The body has been migrated to
:mod:`job_bot.resume_management`; this file is kept as a thin shim
that delegates to the VSA slice and emits a
:class:`DeprecationWarning` on instantiation.

Public surface (CLI flags, namespace, aliases) is preserved verbatim
so the existing ``hh-applicant-tool clone-resume …`` command
continues to work.
"""

from __future__ import annotations

import argparse
import logging
import warnings
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

    resume_id: str | None = None


class Operation(BaseOperation):
    """Thin shim that delegates to :class:`job_bot.resume_management.ResumeCloneHandler`.

    Public surface preserved verbatim from the legacy implementation:

    * ``__aliases__`` — ``[]`` (no CLI alias, command name is
      ``clone-resume``).
    * ``setup_parser`` — ``--resume-id`` flag.
    * ``run`` — dispatch to the VSA handler. The legacy operator also
      called ``tool.storage.resumes.save_batch(resumes)`` to refresh
      the local cache; the shim preserves that side effect because
      ``ResumeCloneHandler`` is API-only.
    """

    __aliases__: list[str] = []

    def __init__(self) -> None:
        warnings.warn(
            "hh_applicant_tool.operations.clone_resume is deprecated; "
            "use job_bot.resume_management instead (issue #137).",
            DeprecationWarning,
            stacklevel=2,
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--resume-id",
            help=(
                "Необязательный идентификатор резюме. "
                "Если не указать, то будет клонировано дефолтное (первое)"
            ),
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> int | None:
        """Dispatch the legacy ``clone-resume`` command to the VSA handler."""
        from job_bot.resume_management import create_resume_management_slice

        # Preserve the legacy side effect of refreshing the local
        # resume cache before cloning. The VSA handler does not own
        # a storage repository, so the shim does the save_batch
        # here.
        try:
            resumes = tool.get_resumes()
        except Exception as ex:  # noqa: BLE001 — match legacy tolerance
            logger.error("Не удалось получить список резюме: %s", ex)
            return 1
        try:
            tool.storage.resumes.save_batch(resumes)
        except Exception as ex:  # noqa: BLE001 — match legacy tolerance
            logger.warning("Не удалось обновить локальный кеш резюме: %s", ex)

        slice_ = create_resume_management_slice(api_client=tool.api_client)
        result = slice_.clone_resume.clone(resume_id=args.resume_id)
        if not result.ok:
            return 1
        return None
