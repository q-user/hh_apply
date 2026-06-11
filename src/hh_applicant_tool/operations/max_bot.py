"""CLI-операция ``max-bot`` (issue #58).

Запускает MAX-бот в режиме long polling. Опрашивает ``getUpdates``,
передаёт каждое обновление в ``MaxBotSlice`` через ``on_update`` и
смещает оффсет после успешной итерации. На сетевые ошибки бэкафит
и продолжает работу.

В отличие от ``telegram-bot`` (issue #7), у MAX-бота пока нет
review/digest-флоу — это простой транспорт, и ``MaxBotSlice``
выступает тонкой обёрткой над ``TransportHandler`` плюс
:class:`MaxTransportPort`.

CLI-флаги:
  * ``--once`` — обработать один цикл polling и завершиться (smoke / cron).
  * ``--send-message`` — отправить ``--text`` в ``--chat-id`` и выйти
    (handy для smoke-теста транспорта без реального API).

Wiring (issue #58):
  * Операция принимает ``bot_adapter`` через конструктор
    (DI-инжекция из тестов / из ``AppContainer``). По умолчанию
    собирается в :meth:`run` через :func:`create_max_bot_slice` с
    :class:`RequestsMaxTransport` (placeholder-реализация
    :class:`MaxTransportPort`, живёт в ``job_bot.max_bot.requests_transport``).
  * Вся логика polling/отправки делегирована :class:`MaxBotSlice` —
    операция только парсит аргументы и реагирует на ошибки инициализации.
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING, Any

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    """Аргументы ``max-bot``."""

    once: bool
    send_message: bool
    chat_id: int | None
    text: str | None


class Operation(BaseOperation):
    """Запустить MAX-бот с long polling.

    Args:
        bot_adapter: опциональный VSA-слайс :class:`MaxBotSlice`
            (или любой объект с тем же интерфейсом: ``transport``,
            ``handler``, ``send_message``). Если ``None`` — собирается
            в :meth:`run` поверх ``tool.config`` + ``tool.session``.
    """

    def __init__(self, bot_adapter: Any | None = None) -> None:
        self._bot_adapter = bot_adapter

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help=(
                "Обработать один цикл polling и завершиться. "
                "Удобно для smoke-тестов и cron."
            ),
        )
        parser.add_argument(
            "--send-message",
            action="store_true",
            help=(
                "Отправить одно сообщение и выйти (smoke-тест транспорта). "
                "Требует ``--chat-id`` и ``--text``."
            ),
        )
        parser.add_argument(
            "--chat-id",
            type=int,
            default=None,
            help="ID чата для ``--send-message``.",
        )
        parser.add_argument(
            "--text",
            type=str,
            default=None,
            help="Текст сообщения для ``--send-message``.",
        )

    def run(
        self,
        tool: "HHApplicantTool",
        args: BaseNamespace,
    ) -> int:
        once = bool(getattr(args, "once", False))
        send_message = bool(getattr(args, "send_message", False))

        # Build the adapter lazily so the bot_token check stays
        # colocated with the run() contract (matches telegram-bot).
        adapter = self._bot_adapter or self._build_adapter(tool)
        if adapter is None:
            # ``_build_adapter`` already logged the reason.
            return 1

        if send_message:
            return self._run_send_message(adapter, args)

        return self._run_polling(adapter, once=once)

    # ─── Smoke-режим: одно сообщение и выйти ───────────────────────

    def _run_send_message(
        self, adapter: Any, args: BaseNamespace
    ) -> int:
        chat_id = getattr(args, "chat_id", None)
        text = getattr(args, "text", None)
        if chat_id is None or not text:
            logger.error(
                "--send-message требует --chat-id и --text",
            )
            return 1
        ok = adapter.send_message(chat_id=chat_id, text=text)
        if not ok:
            logger.error("MAX API: send_message вернул False")
            return 1
        logger.info("MAX: сообщение отправлено в chat_id=%s", chat_id)
        return 0

    # ─── Polling-цикл ───────────────────────────────────────────────

    def _run_polling(self, adapter: Any, *, once: bool) -> int:
        mode_label = "single-cycle" if once else "long polling"
        logger.info("MAX bot started (%s)...", mode_label)
        print(f"🤖 MAX bot started ({mode_label}). Press Ctrl+C to stop.")

        # Делегируем polling-цикл VSA ``TransportHandler``. ``stop_after``
        # ограничивает цикл одним батчем в smoke-режиме; в проде
        # ``run()`` крутится бесконечно.
        try:
            adapter.handler.run(stop_after=1 if once else None)
        except KeyboardInterrupt:
            logger.info("MAX bot is shutting down...")
            print("⛔ MAX bot stopped.")
            return 0
        return 0

    # ─── DI ────────────────────────────────────────────────────────

    def _build_adapter(self, tool: "HHApplicantTool") -> Any | None:
        """Собрать :class:`MaxBotSlice` из ``tool`` для рантайма.

        Returns ``None`` if ``max.bot_token`` is missing — ``run()``
        then surfaces exit code 1. The transport lives in the slice
        package (``job_bot.max_bot.requests_transport``) so the
        container can import it without depending on the operations
        module (no layering violation, no circular-import trap).
        """
        from job_bot.max_bot.requests_transport import (
            DEFAULT_API_URL,
            RequestsMaxTransport,
        )
        from job_bot.max_bot.slice import create_max_bot_slice

        max_cfg = tool.config.get("max") or {}
        bot_token = max_cfg.get("bot_token")
        if not bot_token:
            logger.error("max.bot_token не задан в конфиге")
            return None

        api_url = max_cfg.get("api_url") or DEFAULT_API_URL
        transport = RequestsMaxTransport(
            session=tool.session,
            bot_token=bot_token,
            api_url=api_url,
        )
        return create_max_bot_slice(transport=transport)
