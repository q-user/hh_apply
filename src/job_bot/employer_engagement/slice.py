"""Main entry point for the ``employer_engagement`` VSA slice (issue #137).

The slice replaces the legacy ``reply_employers`` operation. It exposes
two public collaborators via the ``.engagement`` property:

* :class:`~job_bot.employer_engagement.handlers.invitation_fetcher.InvitationFetcher`
  — yields invitations that should receive a reply (after applying the
  resume / period / blacklist / only-invitations filters).
* :class:`~job_bot.employer_engagement.handlers.reply_composer.ReplyComposer`
  — composes and sends the reply in template / AI / interactive mode.

Typical wiring (production)::

    from job_bot.employer_engagement import create_employer_engagement_slice
    from job_bot.shared.adapters.hh_api import HHApiAdapter

    api = HHApiAdapter(tool=tool)
    slice_ = create_employer_engagement_slice(
        api=api,
        ai_client=tool.get_cover_letter_ai(system_prompt),
        resumes=tool.get_resumes(),
    )
    slice_.engagement.run(dry_run=False)
"""

from __future__ import annotations

import logging
from typing import Any

from job_bot.employer_engagement.handlers.invitation_fetcher import (
    InvitationFetcher,
)
from job_bot.employer_engagement.handlers.reply_composer import (
    InteractiveReply,
    ReplyComposer,
)
from job_bot.employer_engagement.ports.api_port import (
    AIClientPort,
    EmployerActionsPort,
    MessageSourcePort,
    NegotiationSourcePort,
)

logger = logging.getLogger(__name__)


class EngagementHandler:
    """Top-level orchestrator exposed as ``slice.engagement``.

    Combines the :class:`InvitationFetcher` and
    :class:`ReplyComposer` and exposes a single :meth:`run` method
    that mirrors the legacy operation's ``reply_employers()``.
    """

    def __init__(
        self,
        *,
        fetcher: InvitationFetcher,
        composer: ReplyComposer,
    ) -> None:
        self._fetcher = fetcher
        self._composer = composer

    def run(self, *, dry_run: bool = False) -> int:
        """Iterate eligible invitations and post a reply to each.

        Returns the number of messages (or would-be messages, in
        ``dry_run`` mode) successfully dispatched.
        """
        sent = 0
        for inv in self._fetcher.eligible():
            try:
                if self._composer.reply(inv, dry_run=dry_run):
                    sent += 1
            except Exception:  # noqa: BLE001
                # The legacy code logged and continued on per-chat
                # errors. Preserve that behaviour at the slice
                # boundary so one broken chat doesn't kill the run.
                logger.exception("Failed to reply to negotiation %s", inv.id)
                continue
        logger.info("Engagement run finished: %d replies dispatched", sent)
        return sent


class EmployerEngagementSlice:
    """The ``employer_engagement`` slice (VSA, issue #137).

    Public surface:

    * :attr:`engagement` — :class:`EngagementHandler` (the high-level
      orchestrator).
    * :attr:`fetcher` / :attr:`composer` — the underlying handlers
      (for tests / advanced wiring).
    * :attr:`api` — the API port (read-only access).
    """

    def __init__(
        self,
        *,
        api: Any,
        ai_client: AIClientPort | None = None,
        resumes: list[dict[str, Any]] | None = None,
        resume_id: str | None = None,
        user: dict[str, str] | None = None,
        reply_message: str | None = None,
        system_prompt: str | None = None,
        message_prompt: str | None = None,
        use_ai: bool = False,
        only_invitations: bool = False,
        period: int | None = None,
        max_pages: int = 25,
        blacklisted_employers: set[str] | None = None,
        interactive_reply: InteractiveReply | None = None,
        interactive_user: dict[str, Any] | None = None,
    ) -> None:
        # The ``api`` argument must satisfy the three engagement ports.
        # In production the legacy ``HHApiClient``/``MegaTool`` is
        # wrapped by an adapter; in tests a hand-rolled fake is used.
        self._api = api
        self._resumes = list(resumes or [])
        self._resume_id = resume_id
        self._user = dict(user or {})
        self._reply_message = reply_message
        self._system_prompt = system_prompt
        self._message_prompt = message_prompt
        self._use_ai = use_ai
        self._only_invitations = only_invitations
        self._period = period
        self._max_pages = max_pages
        self._blacklisted = set(blacklisted_employers or ())

        # The legacy operation used ``tool.first_resume_id()`` to pick
        # the resume to look up when ``--resume-id`` wasn't given. We
        # default to the first resume in the list (in publish order).
        self._resume_title = self._pick_resume_title()

        self._fetcher = InvitationFetcher(
            source=api,
            messages=api,
            resumes=self._resumes,
            resume_id=self._resume_id,
            only_invitations=self._only_invitations,
            period=self._period,
            blacklisted_employers=self._blacklisted,
        )
        self._composer = ReplyComposer(
            actions=api,
            messages=api,
            ai_client=ai_client,
            reply_template=reply_message,
            system_prompt=system_prompt,
            message_prompt=message_prompt,
            use_ai=use_ai,
            interactive_reply=interactive_reply,
            user=self._user,
            resume_title=self._resume_title,
        )
        self.engagement = EngagementHandler(
            fetcher=self._fetcher, composer=self._composer
        )

    # ─── Read-only accessors (used by tests) ─────────────────────

    @property
    def api(self) -> Any:
        """The API port passed in (read-only)."""
        return self._api

    @property
    def fetcher(self) -> InvitationFetcher:
        return self._fetcher

    @property
    def composer(self) -> ReplyComposer:
        return self._composer

    # ─── Internals ───────────────────────────────────────────────

    def _pick_resume_title(self) -> str:
        if self._resume_id is not None:
            for r in self._resumes:
                if r.get("id") == self._resume_id:
                    return str(r.get("title") or "")
            return ""
        if self._resumes:
            return str(self._resumes[0].get("title") or "")
        return ""


def create_employer_engagement_slice(
    *,
    api: Any,
    ai_client: AIClientPort | None = None,
    resumes: list[dict[str, Any]] | None = None,
    resume_id: str | None = None,
    user: dict[str, str] | None = None,
    reply_message: str | None = None,
    use_ai: bool = False,
    only_invitations: bool = False,
    period: int | None = None,
    max_pages: int = 25,
    blacklisted_employers: set[str] | None = None,
) -> EmployerEngagementSlice:
    """Factory for :class:`EmployerEngagementSlice`.

    Mirrors the constructor but uses kwargs to make CLI wiring clearer.
    The legacy ``--max-pages`` argument is accepted for back-compat
    with the shim; the slice's own logic does not currently paginate
    (the underlying ``NegotiationSourcePort`` is expected to be
    pre-paginated or, for the legacy ``HHApiClient``, to handle
    pagination internally — see :class:`HHApiAdapter`).
    """
    return EmployerEngagementSlice(
        api=api,
        ai_client=ai_client,
        resumes=resumes,
        resume_id=resume_id,
        user=user,
        reply_message=reply_message,
        use_ai=use_ai,
        only_invitations=only_invitations,
        period=period,
        max_pages=max_pages,
        blacklisted_employers=blacklisted_employers,
    )


# Re-export the ports so callers can do ``from job_bot.employer_engagement
# import NegotiationSourcePort`` without a deeper path.
__all__ = [
    "EmployerEngagementSlice",
    "create_employer_engagement_slice",
    "EngagementHandler",
    "InteractiveReply",
    "NegotiationSourcePort",
    "MessageSourcePort",
    "EmployerActionsPort",
    "AIClientPort",
]
