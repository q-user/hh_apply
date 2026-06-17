"""Channel handler - business logic for channel monitoring."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from job_bot.channel_monitoring.models.channel import (
    Channel,
    ChannelCreate,
)
from job_bot.channel_monitoring.models.vacancy_link import VacancyLink


class ChannelHandler:
    """Handler for channel monitoring and vacancy-link extraction.

    Uses a caller-supplied ``sqlite3.Connection`` (with row factory set to
    :class:`sqlite3.Row`) so the same handler can be backed by an in-memory
    connection in tests and an on-disk database in production.
    """

    _VACANCY_URL_RE = re.compile(
        r"https?://[a-z0-9.-]*hh\.ru/vacancy/(\d+)",
        re.IGNORECASE,
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._seen: set[str] = set()
        self._init_schema()

    # ---- Schema ---------------------------------------------------------

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cm_channels (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                channel_id TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_message_id INTEGER NOT NULL DEFAULT 0,
                filter_keywords TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cm_vacancy_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                vacancy_id TEXT NOT NULL UNIQUE,
                source_channel TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    # ---- CRUD -----------------------------------------------------------

    def add_channel(self, channel: ChannelCreate) -> Channel:
        entity = channel.to_channel()
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO cm_channels
                (id, name, channel_id, enabled, last_message_id,
                 filter_keywords, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity.id,
                entity.name,
                entity.channel_id,
                1 if entity.enabled else 0,
                entity.last_message_id,
                json.dumps(entity.filter_keywords, ensure_ascii=False),
                entity.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return entity

    def remove_channel(self, channel_id: str) -> bool:
        cur = self._conn.cursor()
        cur.execute(
            "DELETE FROM cm_channels WHERE channel_id = ?", (channel_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_channels(self, enabled_only: bool = False) -> list[Channel]:
        cur = self._conn.cursor()
        sql = "SELECT * FROM cm_channels"
        if enabled_only:
            sql += " WHERE enabled = 1"
        cur.execute(sql)
        return [self._row_to_channel(r) for r in cur.fetchall()]

    def get_channel(self, channel_id: str) -> Channel | None:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM cm_channels WHERE channel_id = ?", (channel_id,)
        )
        row = cur.fetchone()
        return self._row_to_channel(row) if row else None

    # ---- Parsing --------------------------------------------------------

    def parse_message(
        self, text: str, source_channel: str, message_id: int
    ) -> list[VacancyLink]:
        return [
            VacancyLink(
                url=match.group(0),
                vacancy_id=match.group(1),
                source_channel=source_channel,
                message_id=message_id,
            )
            for match in self._VACANCY_URL_RE.finditer(text)
        ]

    # ---- Deduplication --------------------------------------------------

    def is_already_processed(self, vacancy_id: str) -> bool:
        if vacancy_id in self._seen:
            return True
        cur = self._conn.cursor()
        cur.execute(
            "SELECT 1 FROM cm_vacancy_links WHERE vacancy_id = ?",
            (vacancy_id,),
        )
        return cur.fetchone() is not None

    def mark_processed(self, link: VacancyLink) -> None:
        """Persist a processed vacancy link (used by callers / tests)."""
        self._seen.add(link.vacancy_id)
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO cm_vacancy_links
                (url, vacancy_id, source_channel, message_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                link.url,
                link.vacancy_id,
                link.source_channel,
                link.message_id,
                link.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    def update_last_message_id(
        self, channel_id: str, last_message_id: int
    ) -> bool:
        """Update the channel's ``last_message_id`` watermark (issue #61).

        Returns ``True`` when the row was updated, ``False`` when the
        channel id is unknown or the new value isn't strictly greater
        than the stored one (we never rewind the watermark).
        """
        if last_message_id <= 0:
            return False
        cur = self._conn.cursor()
        cur.execute(
            """
            UPDATE cm_channels
               SET last_message_id = ?
             WHERE channel_id = ?
               AND last_message_id < ?
            """,
            (last_message_id, channel_id, last_message_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _row_to_channel(row: Any) -> Channel:
        return Channel(
            id=row["id"],
            name=row["name"],
            channel_id=row["channel_id"],
            enabled=bool(row["enabled"]),
            last_message_id=row["last_message_id"],
            filter_keywords=json.loads(row["filter_keywords"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
