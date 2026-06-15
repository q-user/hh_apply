"""Legacy ``clear-negotiations`` operation — DEPRECATED shim (issue #137).

The "decline old / discarded / refused negotiations" workflow now
lives in the :mod:`job_bot.negotiations.lifecycle` VSA sub-slice.
This module is preserved as a deprecation shim so the CLI parser
(which iterates every ``operations/`` module to build the sub-parser
list) keeps registering the ``clear-negotiations`` sub-command
unchanged. New code should depend on the VSA sub-slice directly.

.. deprecated:: 1.9
   Use :class:`job_bot.negotiations.lifecycle.NegotiationLifecycleSlice`
   (or :func:`job_bot.negotiations.lifecycle.create_negotiation_lifecycle_slice`)
   instead. This module is part of the VSA switchover (issue #137) and
   **planned for removal in version 2.0**. New code should depend on
   the new slice; this shim is kept for backward compatibility only.

Извлечено из ``operations/clear_negotiations.py``: команда удаляет
отказы и/или старые отклики; опционально удаляет чаты и блокирует
работодателей. Из-за особенностей API её иногда нужно вызывать
больше одного раза.
"""

from __future__ import annotations

import argparse
import logging
import warnings
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import requests

from job_bot.negotiations.lifecycle import create_negotiation_lifecycle_slice

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)


# ─── Public types (preserved for back-compat) ─────────────────────


class Namespace(BaseNamespace):
    """Argparse namespace mirroring the legacy ``clear_negotiations`` flags."""

    cleanup: bool
    blacklist_discard: bool
    older_than: int
    dry_run: bool
    delete_chat: bool
    block_ats: bool


# ─── Tool adapter (legacy ``HHApplicantTool`` → ``LifecycleApiPort``) ─


class _ToolAdapter:
    """Adapts a legacy :class:`HHApplicantTool` to ``LifecycleApiPort``.

    The lifecycle sub-slice depends on a single port that wraps
    ``api_client.delete / put`` plus the web-trash endpoint used to
    hide a chat. The legacy tool exposes those via
    ``tool.api_client`` and ``tool.session``; this adapter
    translates them.
    """

    def __init__(self, tool: "HHApplicantTool") -> None:
        self._tool = tool

    def iter_negotiations(self, status: str = "all") -> Iterable[Any]:
        yield from self._tool.get_negotiations(status=status)

    def decline_negotiation(
        self,
        negotiation_id: str,
        *,
        with_decline_message: bool,
    ) -> None:
        self._tool.api_client.delete(
            f"/negotiations/active/{negotiation_id}",
            with_decline_message=with_decline_message,
        )

    def blacklist_employer(self, employer_id: str) -> None:
        self._tool.api_client.put(f"/employers/blacklisted/{employer_id}")

    def delete_chat(self, topic: int | str) -> bool:
        """Чат можно удалить только через веб-версию (XSRF-protected POST)."""
        headers = {
            "X-Hhtmfrom": "main",
            "X-Hhtmsource": "negotiation_list",
            "X-Requested-With": "XMLHttpRequest",
            "X-Xsrftoken": self._tool.xsrf_token,
            "Refrerer": (
                "https://hh.ru/applicant/negotiations"
                "?hhtmFrom=main&hhtmFromLabel=header"
            ),
        }
        payload = {
            "topic": topic,
            "query": "?hhtmFrom=main&hhtmFromLabel=header",
            "substate": "HIDE",
        }
        try:
            r = self._tool.session.post(
                "https://hh.ru/applicant/negotiations/trash",
                payload,
                headers=headers,
            )
            r.raise_for_status()
            return True
        except requests.RequestException as ex:
            logger.error(ex)
            return False


# ─── Operation (preserved for back-compat) ───────────────────────


class Operation(BaseOperation):
    """Удалить отказы и/или старые отклики.

    Thin adapter that delegates to the ``negotiations.lifecycle`` VSA
    sub-slice. All CLI flags and aliases from the original operation
    are preserved verbatim.
    """

    __aliases__ = ["clear-negotiations", "delete-negotiations"]

    def __init__(self) -> None:
        # Per the deprecation contract (issue #92): warn at
        # instantiation with ``stacklevel=2`` so the warning points at
        # the caller (the user code that instantiated the Operation),
        # not at the shim.
        warnings.warn(
            "hh_applicant_tool.operations.clear_negotiations is deprecated; "
            "use job_bot.negotiations.lifecycle instead (issue #137).",
            DeprecationWarning,
            stacklevel=2,
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-b",
            "--blacklist-discard",
            "--blacklist",
            action=argparse.BooleanOptionalAction,
            help="Блокировать работодателя за отказ",
        )
        parser.add_argument(
            "-o",
            "--older-than",
            type=int,
            help="Удаляет любые отклики старше N дней",
        )
        parser.add_argument(
            "-d",
            "--delete-chat",
            action="store_true",
            help="Удалить так же чат",
        )
        parser.add_argument(
            "--block-ats", action="store_true", help="Блокировать ATS"
        )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Тестовый запуск без реального удаления",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        adapter = _ToolAdapter(tool)
        slice_ = create_negotiation_lifecycle_slice(
            api=adapter,  # type: ignore[arg-type]
            blacklisted_employers=set(tool.get_blacklisted()),
        )
        result = slice_.run(
            older_than=args.older_than,
            blacklist_discard=bool(args.blacklist_discard),
            delete_chat=bool(args.delete_chat),
            block_ats=bool(args.block_ats),
            dry_run=bool(args.dry_run),
        )
        # Preserve the legacy CLI completion line.
        print("✅ Удаление откликов завершено.")
        # The slice returns a structured result; expose it on ``self``
        # so callers (and the CLI introspection) can read it. Mirrors
        # the legacy ``self.tool = tool; self.args = args`` pattern.
        self.result = result
        self.args = args


__all__ = ["Namespace", "Operation"]
