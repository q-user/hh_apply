"""TelegramBotSlice -- main entry point and factory.

The slice aggregates the command / digest / review handlers, exposes them
through small ports and re-uses the existing ``TelegramTransport``,
``DailyDigestService`` and ``ReviewFlowService`` from
``job_bot.telegram_bot.services`` (VSA — issue #87).

Usage::

    from job_bot.telegram_bot.slice import create_telegram_bot_slice
    from job_bot.telegram_bot.telegram_transport import TelegramTransport

    transport = TelegramTransport(config=cfg)
    slice_ = create_telegram_bot_slice(database=db, transport=transport)
    slice_.service.dispatch_update(update_dict)
"""

from __future__ import annotations

import logging
from typing import Any, cast

from job_bot.shared.storage.database import Database
from job_bot.telegram_bot.ports.digest_port import DailyDigestPort
from job_bot.telegram_bot.ports.review_port import ReviewFlowPort
from job_bot.telegram_bot.ports.transport_port import TelegramTransportPort
from job_bot.telegram_bot.services.bot_service import BotService

logger = logging.getLogger(__package__)


def _default_digest_service(storage: Any, transport: Any, config: Any) -> Any:
    """Build a :class:`DailyDigestService` from the slice's dependencies."""
    from hh_applicant_tool.storage import StorageFacade
    from job_bot.telegram_bot.services.daily_digest_service import (
        DailyDigestService,
    )

    return DailyDigestService(
        storage=StorageFacade(storage),
        transport=transport,
        config=config,
    )


def _default_review_service(storage: Any, transport: Any, config: Any) -> Any:
    """Build a :class:`ReviewFlowService` from the slice's dependencies."""
    from hh_applicant_tool.storage import StorageFacade
    from job_bot.telegram_bot.services.review_service import ReviewFlowService

    return ReviewFlowService(
        storage=StorageFacade(storage),
        transport=transport,
        config=config,
    )


class TelegramBotSlice:
    """Aggregates command, digest, review and transport for the Telegram bot.

    The slice intentionally keeps references to the underlying services
    (digest / review) so callers (CLI, tests) can introspect them. The
    high-level :class:`BotService` is the single entry point for
    dispatching updates.
    """

    def __init__(
        self,
        *,
        database: Database | Any,
        transport: TelegramTransportPort | Any,
        config: Any = None,
        digest_service: Any | None = None,
        review_service: Any | None = None,
    ) -> None:
        self._database = database
        self._transport = transport
        self._config = config or {}

        # The ``Database`` wrapper exposes ``.connect()`` rather than
        # holding a single connection. The legacy services in
        # ``hh_applicant_tool`` expect a long-lived ``sqlite3.Connection``,
        # so we open one and store the context-manager so we can close
        # it on shutdown. If a raw ``sqlite3.Connection`` is passed, it's
        # used as-is.
        self._storage, self._storage_ctx = self._resolve_storage(database)

        # Build the default services unless the caller provided mocks.
        self._digest_service = (
            digest_service
            if digest_service is not None
            else _default_digest_service(self._storage, transport, self._config)
        )
        self._review_service = (
            review_service
            if review_service is not None
            else _default_review_service(self._storage, transport, self._config)
        )

        self._service = BotService(
            storage=self._storage,
            transport=transport,
            digest_service=self._digest_service,
            review_service=self._review_service,
        )

    # ─── Public surface ───────────────────────────────────────

    @property
    def database(self) -> Any:
        return self._database

    @property
    def transport(self) -> TelegramTransportPort | Any:
        return self._transport

    @property
    def service(self) -> BotService:
        return self._service

    @property
    def digest(self) -> DailyDigestPort:
        """Daily-digest port (delegates to the default :class:`DailyDigestService`)."""
        return cast("DailyDigestPort", self._digest_service)

    @property
    def review(self) -> ReviewFlowPort:
        """Review-flow port (delegates to the default :class:`ReviewFlowService`)."""
        return cast("ReviewFlowPort", self._review_service)

    @property
    def commands(self) -> Any:
        """Underlying :class:`CommandHandler` (for direct access from tests)."""
        return self._service.commands

    def close(self) -> None:
        """Release any owned resources (the long-lived DB connection)."""
        if self._storage_ctx is not None:
            try:
                self._storage_ctx.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to close storage context")
            self._storage_ctx = None

    # ─── Internals ────────────────────────────────────────────

    @staticmethod
    def _resolve_storage(database: Any) -> tuple[Any, Any]:
        """Return ``(connection, context_manager)``.

        If ``database`` is a ``Database`` wrapper, we open a long-lived
        connection (and keep the context manager so the slice can close
        it on shutdown). If it's already a ``sqlite3.Connection``, we
        return it as-is and ``None`` for the context.
        """
        from job_bot.shared.storage.database import Database as _DB

        if isinstance(database, _DB):
            ctx = database.connect()
            conn = ctx.__enter__()
            return conn, ctx
        return database, None


def create_telegram_bot_slice(
    *,
    database: Database | Any | None = None,
    transport: TelegramTransportPort | Any,
    config: Any = None,
    digest_service: Any | None = None,
    review_service: Any | None = None,
) -> TelegramBotSlice:
    """Factory for :class:`TelegramBotSlice`.

    Args:
        database: a :class:`job_bot.shared.storage.database.Database` (or a
            raw ``sqlite3.Connection``).
        transport: a :class:`TelegramTransportPort` (typically the
            ``TelegramTransport`` from ``job_bot.telegram_bot.telegram_transport``).
        config: optional config mapping (with a ``telegram`` sub-dict);
            passed to the default digest / review services.
        digest_service: optional override for the daily digest service.
        review_service: optional override for the review state machine.
    """
    if database is None:
        from job_bot.shared.config.settings import (
            Settings,
            load_settings,
        )
        from job_bot.shared.storage.database import create_database

        settings: Settings = load_settings()
        database = create_database(settings.database.path)

    return TelegramBotSlice(
        database=database,
        transport=transport,
        config=config,
        digest_service=digest_service,
        review_service=review_service,
    )
