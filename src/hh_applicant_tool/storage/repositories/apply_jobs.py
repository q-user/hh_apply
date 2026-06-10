from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from ..models.apply_job import ApplyJobModel
from .base import BaseRepository


class ApplyJobsRepository(BaseRepository):
    __table__ = "apply_jobs"
    model = ApplyJobModel
    # Один job на черновик: повторное одобрение не плодит дубликаты в очереди.
    conflict_columns = ("draft_id",)

    def claim_next_job(
        self,
        worker_id: str,
        now_str: str,
        cutoff_str: str,
    ) -> Optional[ApplyJobModel]:
        """Атомарно выбрать и заблокировать следующую задачу (SELECT ... FOR UPDATE).

        Возвращает загруженную модель ApplyJobModel или None, если подходящих нет.
        Использует SELECT ... FOR UPDATE для предотвращения race condition
        при параллельной работе нескольких воркеров.
        """
        sql = f"""
        SELECT * FROM {self.table_name}
        WHERE (
            status = 'queued'
            AND (next_attempt_at IS NULL OR next_attempt_at <= :now)
            AND (
                locked_at IS NULL
                OR locked_by = :worker_id
                OR locked_at < :cutoff
            )
        )
        OR (
            status = 'running'
            AND locked_at < :cutoff
        )
        ORDER BY
            CASE WHEN status = 'queued' THEN 0 ELSE 1 END,
            rowid
        LIMIT 1 FOR UPDATE;
        """
        cur = self.conn.execute(
            sql,
            {"now": now_str, "worker_id": worker_id, "cutoff": cutoff_str},
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_model(cur, row)

    def lock_job(self, job_id: int, worker_id: str, locked_at: str) -> None:
        """Обновить статус job на running и установить locked_* (внутри транзакции)."""
        sql = f"""
        UPDATE {self.table_name}
        SET status = 'running',
            locked_at = :locked_at,
            locked_by = :worker_id,
            attempts = attempts + 1
        WHERE id = :job_id;
        """
        self.conn.execute(
            sql,
            {"job_id": job_id, "locked_at": locked_at, "worker_id": worker_id},
        )
