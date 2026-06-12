"""BotService -- top-level orchestrator that wires all handlers + transport.

Responsibilities:
  * Decide which handler should receive a given update (command vs
    review-state vs callback).
  * Surface a single :meth:`dispatch_update` entry point for the transport
    loop.
  * Be resilient: handler errors never crash the bot.
"""

from __future__ import annotations

import logging
from typing import Any

from hh_applicant_tool.storage import StorageFacade
from job_bot.telegram_bot.handlers.command_handler import CommandHandler
from job_bot.telegram_bot.handlers.digest_handler import DigestHandler
from job_bot.telegram_bot.handlers.review_handler import ReviewHandler
from job_bot.telegram_bot.models.message import OutgoingMessage
from job_bot.telegram_bot.ports.transport_port import TelegramTransportPort

logger = logging.getLogger(__package__)


# FSM states that expect a free-form text message (review is in flight).
_TEXT_INPUT_STATES = frozenset(
    {
        "awaiting_test_regen_comment",
        "awaiting_custom_test_answer",
        "awaiting_cover_letter_regen_comment",
        "awaiting_custom_cover_letter",
    }
)


def _as_facade(storage: Any) -> Any:
    """Return a :class:`StorageFacade` for ``storage``, wrapping if needed.

    Idempotent: if ``storage`` is already a :class:`StorageFacade`, it
    is returned as-is. Single source of truth for the wrap-once
    contract (issue #56 followup) — used by both :class:`BotService`
    and :func:`_session_state_for` so the wrapping logic lives in one
    place.
    """
    from hh_applicant_tool.storage import StorageFacade

    return (
        storage
        if isinstance(storage, StorageFacade)
        else StorageFacade(storage)
    )


def _session_state_for(storage: Any, chat_id: int) -> str:
    """Return the FSM state for ``chat_id``; ``"idle"`` if no session yet.

    Accepts either a raw ``sqlite3.Connection`` or a
    :class:`StorageFacade` (the latter happens when the bot's
    :class:`BotService` already wrapped the connection — wrapping it
    again would try to pass a ``StorageFacade`` to a repository
    constructor, which fails). Issue #56 followup.
    """
    try:
        facade = _as_facade(storage)
        session = facade.telegram_sessions.get(chat_id)
    except Exception:  # noqa: BLE001
        return "idle"
    if session is None:
        return "idle"
    return getattr(session, "state", "idle") or "idle"


def _extract_chat_id(update: dict[str, Any]) -> int | None:
    """Return the chat id from a ``message`` or ``callback_query`` update."""
    message = update.get("message") or {}
    if message:
        chat = message.get("chat") or {}
        cid = chat.get("id")
        if cid is not None:
            return int(cid)
    cb = update.get("callback_query") or {}
    if cb:
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None:
            return int(cid)
    return None


class BotService:
    """Orchestrates the command / digest / review handlers and the transport."""

    def __init__(
        self,
        *,
        storage: Any,
        transport: TelegramTransportPort,
        digest_service: Any,
        review_service: Any,
    ) -> None:
        # Public ``storage`` property returns whatever was passed in
        # (raw ``sqlite3.Connection`` or ``StorageFacade``) — this is
        # the contract ``tests/vsa/test_telegram_bot_slice.py::test_create_service``
        # pins. Internally the handlers need the ``StorageFacade``-style
        # repository access (``.negotiations`` / ``.skipped_vacancies``
        # / ``.application_drafts``), so we build it once as a private
        # helper and forward it to the handlers. The classmethod
        # ``StorageFacade.create`` (issue #56 followup) keeps this
        # pattern ``StoragePort``-compatible.
        self._storage = storage
        self._facade: StorageFacade = _as_facade(storage)
        self._transport = transport
        self._commands = CommandHandler(
            storage=self._facade,
            transport=transport,
            digest_service=digest_service,
            review_service=review_service,
        )
        self._digest = DigestHandler(
            storage=self._facade,
            transport=transport,
            digest_service=digest_service,
        )
        self._review = ReviewHandler(
            storage=self._facade,
            transport=transport,
            review_service=review_service,
        )

    # ─── Public surface ───────────────────────────────────────

    @property
    def storage(self) -> Any:
        return self._storage

    @property
    def transport(self) -> TelegramTransportPort:
        return self._transport

    @property
    def commands(self) -> CommandHandler:
        return self._commands

    @property
    def digest(self) -> DigestHandler:
        return self._digest

    @property
    def review(self) -> ReviewHandler:
        return self._review

    def dispatch_update(self, update: dict[str, Any]) -> OutgoingMessage | None:
        """Dispatch a single update to the right handler.

        Routing rules:
          * ``callback_query`` -> review handler.
          * text update with an active review-state session -> review handler.
          * ``/review`` / ``/cancel`` -> review handler (via command).
          * everything else -> command handler.

        Returns the :class:`OutgoingMessage` produced by the command
        handler (if any) so callers / tests can inspect it. The service
        itself is responsible for actually sending the message.
        """
        try:
            return self._dispatch_safely(update)
        except Exception:  # noqa: BLE001 - never crash the bot
            logger.exception("Unhandled error dispatching update: %s", update)
            return None

    # ─── Internals ────────────────────────────────────────────

    def _dispatch_safely(
        self, update: dict[str, Any]
    ) -> OutgoingMessage | None:
        # Callbacks always go to the review service.
        if update.get("callback_query"):
            try:
                self._review.process_callback(update)
            except Exception:  # noqa: BLE001
                logger.exception("Review callback failed")
            return None

        message = update.get("message")
        if not message:
            return None

        chat_id = _extract_chat_id(update)
        if chat_id is None:
            return None

        # If we're inside a text-input FSM state, route to the review
        # service -- the user is typing an answer to a regen/custom prompt.
        state = _session_state_for(self._storage, chat_id)
        if state in _TEXT_INPUT_STATES:
            try:
                self._review.process_message(update)
            except Exception:  # noqa: BLE001
                logger.exception("Review message failed")
            return None

        # Otherwise the command handler decides. It already ships its
        # own outgoing message via the transport, so we just return
        # the DTO for inspection.
        try:
            return self._commands.handle(update)
        except Exception:  # noqa: BLE001
            logger.exception("Command handler failed")
            return None
