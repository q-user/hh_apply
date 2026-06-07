from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

QUERIES_PATH: Path = Path(__file__).parent / "queries"
MIGRATION_PATH: Path = QUERIES_PATH / "migrations"


logger: logging.Logger = logging.getLogger(__package__)


def init_db(conn: sqlite3.Connection) -> None:
    """Создает схему БД и настраивает connection-уровневые PRAGMA.

    Включает WAL-режим и busy_timeout — это требование для одновременной
    записи из нескольких процессов (collector, telegram-bot, apply-worker)
    в один файл SQLite. PRAGMA journal_mode персистится в файле БД,
    но дёргаем на каждом соединении для надёжности (например, in-memory БД).
    """
    # Настраиваем ПЕРЕД executescript — схема может полагаться на PRAGMA.
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except sqlite3.Error as e:
        logger.warning("PRAGMA setup failed: %s", e)

    changes_before = conn.total_changes

    conn.executescript(
        (QUERIES_PATH / "schema.sql").read_text(encoding="utf-8")
    )

    if conn.total_changes > changes_before:
        logger.info("Применена схема бд")
    # else:
    #     logger.debug("База данных не изменилась.")


def list_migrations() -> list[str]:
    """Выводит имена миграций без расширения, отсортированные по дате"""
    if not MIGRATION_PATH.exists():
        return []
    return sorted([f.stem for f in MIGRATION_PATH.glob("*.sql")])


def apply_migration(conn: sqlite3.Connection, name: str) -> None:
    """Находит файл по имени и выполняет его содержимое"""
    conn.executescript(
        (MIGRATION_PATH / f"{name}.sql").read_text(encoding="utf-8")
    )


# def model2table(o: type) -> str:
#     name: str = o.__name__
#     if name.endswith("Model"):
#         name = name[:-5]
#     return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
