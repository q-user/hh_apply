"""CLI-операция ``channel-monitor`` (issue #57).

Управляет списком отслеживаемых Telegram-каналов и парсит входящие
сообщения на наличие ссылок на вакансии ``hh.ru``.

Делегирует всю бизнес-логику VSA-слайсу
:class:`job_bot.channel_monitoring.slice.ChannelMonitorSlice`.
Операция только парсит аргументы CLI и маршрутизирует команды.

CLI-флаги:
  * ``--list`` (``-l``)  — вывести все каналы (с флагом ``--enabled`` —
                           только активные).
  * ``--add`` (``-a``)    — добавить канал (``--name``, ``--channel-id``,
                           опционально ``--keywords``, разделённые
                           запятыми).
  * ``--remove`` (``-r``) — удалить канал по ``--channel-id``.
  * ``--parse`` (``-p``)  — распарсить ``--text`` и вывести найденные
                           ссылки на вакансии (smoke-тест парсера).

Wiring (issue #57):
  * ``Operation(slice_=...)`` — DI-инжекция слайса. Если ``None`` —
    собирается в :meth:`run` из ``tool.db``.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import TYPE_CHECKING, Any

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    """Аргументы ``channel-monitor``."""

    list: bool
    enabled: bool
    add: bool
    name: str | None
    channel_id: str | None
    keywords: str | None
    remove: bool
    parse: bool
    text: str | None


class Operation(BaseOperation):
    """Запустить / управлять channel-monitoring slice.

    Args:
        slice_: опциональный :class:`ChannelMonitorSlice` (для DI-инжекции
            в тестах / из :class:`AppContainer`).
    """

    def __init__(self, slice_: Any | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "-l",
            "--list",
            action="store_true",
            help="Показать список отслеживаемых каналов.",
        )
        group.add_argument(
            "-a",
            "--add",
            action="store_true",
            help="Добавить новый канал (требует --name и --channel-id).",
        )
        group.add_argument(
            "-r",
            "--remove",
            action="store_true",
            help="Удалить канал по --channel-id.",
        )
        group.add_argument(
            "-p",
            "--parse",
            action="store_true",
            help="Распарсить --text и вывести найденные ссылки (smoke-тест).",
        )

        parser.add_argument(
            "--enabled",
            action="store_true",
            help="В паре с --list: показать только активные каналы.",
        )
        parser.add_argument("--name", help="Имя канала (для --add).")
        parser.add_argument(
            "--channel-id", help="Telegram channel id (для --add/--remove)."
        )
        parser.add_argument(
            "--keywords",
            help="Ключевые слова через запятую (для --add).",
        )
        parser.add_argument(
            "--text",
            help="Текст для --parse (smoke-тест парсера ссылок).",
        )

    def run(
        self,
        tool: "HHApplicantTool",
        args: BaseNamespace,
    ) -> int:
        slice_ = self._slice or self._build_slice(tool)

        if getattr(args, "list", False):
            return self._list(
                slice_, only_enabled=getattr(args, "enabled", False)
            )
        if getattr(args, "add", False):
            return self._add(
                slice_,
                name=getattr(args, "name", None),
                channel_id=getattr(args, "channel_id", None),
                keywords=getattr(args, "keywords", None),
            )
        if getattr(args, "remove", False):
            return self._remove(
                slice_, channel_id=getattr(args, "channel_id", None)
            )
        if getattr(args, "parse", False):
            return self._parse(slice_, text=getattr(args, "text", None))

        # Дефолт: показать справку (как ``-h``).
        self._print_help()
        return 0

    # ─── Подкоманды ─────────────────────────────────────────────────

    def _list(self, slice_: Any, *, only_enabled: bool) -> int:
        channels = slice_.channels.list_channels(enabled_only=only_enabled)
        if not channels:
            print("Нет отслеживаемых каналов.")
            return 0
        for ch in channels:
            mark = "✓" if ch.enabled else "✗"
            kw = ",".join(ch.filter_keywords) if ch.filter_keywords else "-"
            print(f"{mark} {ch.name} ({ch.channel_id})  keywords={kw}")
        return 0

    def _add(
        self,
        slice_: Any,
        *,
        name: str | None,
        channel_id: str | None,
        keywords: str | None,
    ) -> int:
        if not name or not channel_id:
            logger.error("--add требует --name и --channel-id")
            return 1
        from job_bot.channel_monitoring.models.channel import ChannelCreate

        kws = (
            [k.strip() for k in keywords.split(",") if k.strip()]
            if keywords
            else []
        )
        ch = slice_.channels.add_channel(
            ChannelCreate(name=name, channel_id=channel_id, filter_keywords=kws)
        )
        print(
            json.dumps(
                {
                    "id": ch.id,
                    "name": ch.name,
                    "channel_id": ch.channel_id,
                    "enabled": ch.enabled,
                    "filter_keywords": ch.filter_keywords,
                },
                ensure_ascii=False,
            )
        )
        return 0

    def _remove(self, slice_: Any, *, channel_id: str | None) -> int:
        if not channel_id:
            logger.error("--remove требует --channel-id")
            return 1
        ok = slice_.channels.remove_channel(channel_id)
        if not ok:
            logger.error("Канал %s не найден", channel_id)
            return 1
        print(f"Удалён канал {channel_id}")
        return 0

    def _parse(self, slice_: Any, *, text: str | None) -> int:
        if not text:
            logger.error("--parse требует --text")
            return 1
        links = slice_.channels.parse_message(
            text=text, source_channel="cli", message_id=0
        )
        if not links:
            print("Ссылки на вакансии не найдены.")
            return 0
        for link in links:
            print(
                json.dumps(
                    {
                        "url": link.url,
                        "vacancy_id": link.vacancy_id,
                        "source_channel": link.source_channel,
                        "message_id": link.message_id,
                    },
                    ensure_ascii=False,
                )
            )
        return 0

    # ─── DI ────────────────────────────────────────────────────────

    def _build_slice(self, tool: "HHApplicantTool") -> Any:
        """Собрать :class:`ChannelMonitorSlice` из ``tool.db``."""
        from job_bot.channel_monitoring.slice import (
            create_channel_monitor_slice,
        )

        return create_channel_monitor_slice(conn=tool.db)

    @staticmethod
    def _print_help() -> None:
        print(
            "Использование:\n"
            "  channel-monitor --list [--enabled]\n"
            "  channel-monitor --add --name <NAME> --channel-id <ID> [--keywords k1,k2]\n"
            "  channel-monitor --remove --channel-id <ID>\n"
            "  channel-monitor --parse --text <TEXT>\n",
        )
