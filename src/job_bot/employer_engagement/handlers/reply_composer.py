"""Composes a reply for a given invitation.

Three reply modes are supported (in priority order):

1. **template** — the user supplied a static template; placeholders
   are filled in with vacancy/employer/resume metadata.
2. **AI** — when ``use_ai`` is true and an :class:`AIClientPort` is
   configured, the prompt is sent to the AI client. The
   :class:`MessageRecord` history of the negotiation is included as
   context (the last 10 messages, mirroring the legacy behaviour).
3. **interactive** — the slice prompts the user via standard input
   (the legacy operation used ``readline`` history for ``/ban`` and
   ``/cancel`` shortcuts). In production the slice's caller is
   expected to wire a real prompt function; in tests the default
   callable can be overridden to return a canned answer.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from typing import Protocol

from job_bot.employer_engagement.models.invitation import (
    Invitation,
    MessageRecord,
)
from job_bot.employer_engagement.ports.api_port import (
    AIClientPort,
    EmployerActionsPort,
    MessageSourcePort,
)
from job_bot.shared.utils.text import rand_text

logger = logging.getLogger(__name__)


class InteractiveReply(Protocol):
    """Pluggable prompt for the interactive reply mode.

    Return a non-empty string to send, an empty string to skip the
    chat, or a string starting with ``/ban`` / ``/cancel`` to take
    that legacy shortcut.
    """

    def __call__(self, inv: Invitation, history: list[str]) -> str: ...


def _default_interactive_reply(inv: Invitation, history: list[str]) -> str:
    """Stub interactive prompt — used only when no callable is supplied."""
    raise RuntimeError(
        "interactive reply mode requires a prompt callable; "
        "pass `interactive_reply=` to the slice to wire one"
    )


class ReplyComposer:
    """Selects and sends the right reply for a given invitation."""

    def __init__(
        self,
        *,
        actions: EmployerActionsPort,
        messages: MessageSourcePort,
        ai_client: AIClientPort | None = None,
        reply_template: str | None = None,
        system_prompt: str | None = None,
        message_prompt: str | None = None,
        use_ai: bool = False,
        interactive_reply: InteractiveReply | None = None,
        user: dict[str, str] | None = None,
        resume_title: str = "",
        random_delay: Callable[[float, float], float] | None = None,
    ) -> None:
        self._actions = actions
        self._messages = messages
        self._ai = ai_client if use_ai else None
        self._template = reply_template
        self._system_prompt = system_prompt
        self._message_prompt = (
            message_prompt
            or "Напиши короткий ответ работодателю на основе истории переписки."
        )
        self._interactive = interactive_reply or _default_interactive_reply
        self._user = user or {}
        self._resume_title = resume_title
        self._random_delay = random_delay or random.uniform

    def reply(self, inv: Invitation, *, dry_run: bool) -> bool:
        """Compose a reply and post it (unless ``dry_run``).

        Returns ``True`` if a message was (or would be) sent.
        """
        history = self._build_history(inv)
        if not history:
            return False

        last_message = history[-1]
        is_employer_message = last_message.is_from_employer

        # Only reply to a chat where the employer just spoke, or the
        # applicant hasn't seen the last message yet (matches the
        # legacy ``reply_employers` logic exactly).
        if not (is_employer_message or not inv.viewed_by_opponent):
            return False

        text = self._compose_text(inv, history)
        if not text:
            return False

        if text.startswith("/ban"):
            self._actions.blacklist_employer(inv.employer_id)
            return True
        if text.startswith("/cancel"):
            # The legacy decline path is owned by the
            # ``negotiations.lifecycle`` slice; the engagement slice
            # only handles the "send a message" path. The
            # ``/cancel`` shortcut therefore just consumes the chat
            # without sending. (Tests cover the silent-skip case.)
            return True

        if dry_run:
            logger.debug(
                "dry-run: would reply to %s with: %s",
                inv.vacancy_alternate_url,
                text,
            )
            return True

        self._actions.post_message(
            inv.id,
            text=text,
            delay=self._random_delay(1.0, 3.0),
        )
        return True

    # ─── Internals ───────────────────────────────────────────────

    def _build_history(self, inv: Invitation) -> list[MessageRecord]:
        """Return the message history of *inv*, in order."""
        out: list[MessageRecord] = []
        for raw in self._messages.iter_messages(inv.id):
            if isinstance(raw, MessageRecord):
                out.append(raw)
                continue
            if not isinstance(raw, dict):
                continue
            text = raw.get("text")
            if not text:
                continue
            author = raw.get("author") or {}
            out.append(
                MessageRecord(
                    id=str(raw.get("id", "")),
                    text=str(text),
                    author_type=str(author.get("participant_type", "employer")),
                    created_at=str(raw.get("created_at", "")),
                )
            )
        return out

    def _compose_text(
        self, inv: Invitation, history: list[MessageRecord]
    ) -> str:
        if self._template:
            return self._render_template(inv)
        if self._ai is not None:
            return self._compose_with_ai(inv, history)
        return self._interactive(
            inv, [self._format_history_line(m) for m in history]
        )

    def _render_template(self, inv: Invitation) -> str:
        placeholders = inv.placeholder_dict(
            first_name=self._user.get("first_name", ""),
            last_name=self._user.get("last_name", ""),
            email=self._user.get("email", ""),
            phone=self._user.get("phone", ""),
            resume_title=self._resume_title,
        )
        assert self._template is not None  # for type-checkers
        rendered = rand_text(self._template) % placeholders
        logger.debug("Template message: %s", rendered)
        return rendered

    def _compose_with_ai(
        self, inv: Invitation, history: list[MessageRecord]
    ) -> str:
        # The legacy code only used the last 10 messages as context
        recent = history[-10:]
        recent_lines = "\n".join(self._format_history_line(m) for m in recent)
        query = (
            f"Вакансия: {inv.vacancy_name}\n"
            f"История переписки:\n{recent_lines}\n\n"
            f"Инструкция: {self._message_prompt}"
        )
        assert self._ai is not None  # for type-checkers
        return self._ai.complete(query)

    @staticmethod
    def _format_history_line(m: MessageRecord) -> str:
        return f"[ {m.created_at} ] {m.author_type}: {m.text}"
