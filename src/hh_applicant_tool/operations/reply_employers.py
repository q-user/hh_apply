"""Legacy ``reply-employers`` operation — DEPRECATED shim (issue #137).

The "reply to employer chats" workflow now lives in the
:mod:`job_bot.employer_engagement` VSA slice. This module is preserved
as a deprecation shim so the CLI parser (which iterates every
``operations/`` module to build the sub-parser list) keeps registering
the ``reply-employers`` sub-command unchanged. New code should depend
on the VSA slice directly.

.. deprecated:: 1.9
   Use :class:`job_bot.employer_engagement.EmployerEngagementSlice`
   (or :func:`job_bot.employer_engagement.create_employer_engagement_slice`)
   instead. This module is part of the VSA switchover (issue #137) and
   **planned for removal in version 2.0**. New code should depend on
   the new slice; this shim is kept for backward compatibility only.
"""

from __future__ import annotations

import argparse
import logging
import warnings
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from job_bot.employer_engagement import create_employer_engagement_slice

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)


# ─── Public types (preserved for back-compat) ─────────────────────


class Namespace(BaseNamespace):
    """Argparse namespace mirroring the legacy ``reply_employers`` flags."""

    reply_message: str
    max_pages: int
    only_invitations: bool
    dry_run: bool
    use_ai: bool
    system_prompt: str
    message_prompt: str
    period: int


# ─── Tool adapter (legacy ``HHApplicantTool`` → VSA ports) ───────


class _ToolAdapter:
    """Adapts a legacy :class:`HHApplicantTool` to the VSA ports.

    The ``employer_engagement`` slice depends on four ports
    (:class:`NegotiationSourcePort`, :class:`MessageSourcePort`,
    :class:`EmployerActionsPort`, :class:`AIClientPort`). The legacy
    tool doesn't implement them directly, so this adapter translates
    ``tool.api_client`` / ``tool.get_negotiations()`` /
    ``tool.get_cover_letter_ai(...)`` to the port calls.
    """

    def __init__(
        self,
        tool: "HHApplicantTool",
        *,
        system_prompt: str,
        use_ai: bool,
    ) -> None:
        self._tool = tool
        self._system_prompt = system_prompt
        self._use_ai = use_ai
        self._ai_client: Any | None = None

    def _ai(self) -> Any | None:
        if not self._use_ai:
            return None
        if self._ai_client is None:
            self._ai_client = self._tool.get_cover_letter_ai(
                self._system_prompt
            )
        return self._ai_client

    def iter_negotiations(
        self, status: str = "active"
    ) -> Iterable[dict[str, Any]]:
        yield from self._tool.get_negotiations()

    def iter_messages(self, negotiation_id: str) -> Iterable[dict[str, Any]]:
        page = 0
        while True:
            res = self._tool.api_client.get(
                f"/negotiations/{negotiation_id}/messages", page=page
            )
            items = res.get("items") or []
            yield from items
            if page + 1 >= res.get("pages", 0):
                break
            page += 1

    def post_message(
        self,
        negotiation_id: str,
        *,
        text: str,
        delay: float | None = None,
    ) -> None:
        # Back-compat: the legacy ``MegaTool`` accepted ``message=`` and
        # ``data=`` interchangeably; ``HHApiClient.post`` itself only
        # honours ``data=`` so we use that.
        self._tool.api_client.post(
            f"/negotiations/{negotiation_id}/messages",
            data={"message": text},
        )

    def blacklist_employer(self, employer_id: str) -> None:
        self._tool.api_client.put(f"/employers/blacklisted/{employer_id}")

    def complete(self, query: str) -> str:
        client = self._ai()
        if client is None:  # pragma: no cover - defensive
            raise RuntimeError("AI client requested but not configured")
        return client.complete(query)


# ─── Operation (preserved for back-compat) ───────────────────────


class Operation(BaseOperation):
    """Ответ всем работодателям.

    Thin adapter that delegates to the ``employer_engagement`` VSA
    slice. All CLI flags and aliases from the original operation are
    preserved verbatim.
    """

    __aliases__ = ["reply-empls", "reply-chats", "reall"]

    def __init__(self) -> None:
        # Per the deprecation contract (issue #92): warn at instantiation
        # with ``stacklevel=2`` so the warning points at the caller
        # (the user code that instantiated the Operation), not at the
        # shim. Module-level emission is intentionally avoided so
        # importing the module (e.g. for the CLI parser walk) doesn't
        # pollute every test run.
        warnings.warn(
            "hh_applicant_tool.operations.reply_employers is deprecated; "
            "use job_bot.employer_engagement instead (issue #137).",
            DeprecationWarning,
            stacklevel=2,
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--resume-id",
            help="Идентификатор резюме. Если не указан, то просматриваем чаты для всех резюме",
        )
        parser.add_argument(
            "-m",
            "--reply-message",
            "--reply",
            help="Отправить сообщение во все чаты. Если не передать сообщение, то нужно будет вводить его в интерактивном режиме.",  # noqa: E501
        )
        parser.add_argument(
            "--period",
            type=int,
            help="Игнорировать отклики, которые не обновлялись больше N дней",
        )
        parser.add_argument(
            "-p",
            "--max-pages",
            type=int,
            default=25,
            help="Максимальное количество страниц для проверки",
        )
        parser.add_argument(
            "-oi",
            "--only-invitations",
            help="Отвечать только на приглашения",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--dry-run",
            "--dry",
            help="Не отправлять сообщения, а только выводить параметры запроса",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--use-ai",
            "--ai",
            help="Использовать AI для автоматической генерации ответов",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--system-prompt",
            "--ai-system",
            help="Системный промпт для AI",
            default="Ты — соискатель на HeadHunter. Отвечай вежливо и кратко.",
        )
        parser.add_argument(
            "--message-prompt",
            "--prompt",
            help="Промпт для генерации сообщения",
            default="Напиши короткий ответ работодателю на основе истории переписки.",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        adapter = _ToolAdapter(
            tool,
            system_prompt=args.system_prompt,
            use_ai=bool(args.use_ai),
        )

        # ``tool.first_resume_id()`` may raise ``IndexError`` when no
        # resumes exist; preserve the legacy behaviour by falling back
        # to ``args.resume_id`` in that case.
        try:
            resume_id = (
                tool.first_resume_id() if not args.resume_id else args.resume_id
            )
        except (IndexError, KeyError):
            resume_id = args.resume_id

        slice_ = create_employer_engagement_slice(
            api=adapter,  # type: ignore[arg-type]
            ai_client=adapter if args.use_ai else None,
            resumes=tool.get_resumes(),
            resume_id=resume_id,
            user=tool.get_me(),
            reply_message=args.reply_message
            or tool.config.get("reply_message"),
            system_prompt=args.system_prompt,
            message_prompt=args.message_prompt,
            use_ai=bool(args.use_ai),
            only_invitations=bool(args.only_invitations),
            period=args.period,
            max_pages=args.max_pages,
            blacklisted_employers=set(tool.get_blacklisted()),
        )
        # ``--dry-run`` is a per-run flag (the slice doesn't track it
        # on the instance).
        slice_.engagement.run(dry_run=bool(args.dry_run))


__all__ = ["Namespace", "Operation"]
