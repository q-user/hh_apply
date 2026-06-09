"""Test vacancy logger infrastructure implementations."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__package__)


class FileTestVacancyLogger:
    """File-based test vacancy logger with thread-safe writes."""

    def __init__(
        self,
        file_path: str | Path = "vacancies_with_tests.txt",
        *,
        max_file_size: int = 10 * 1024 * 1024,  # 10 MB
        max_files: int = 5,
        encoding: str = "utf-8",
    ) -> None:
        """Initialize file test vacancy logger.

        Args:
            file_path: Path to log file.
            max_file_size: Maximum file size before rotation (bytes).
            max_files: Maximum number of rotated files to keep.
            encoding: File encoding.
        """
        self._file_path = Path(file_path)
        self._max_file_size = max_file_size
        self._max_files = max_files
        self._encoding = encoding
        self._lock = threading.Lock()

        # Ensure parent directory exists
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self, vacancy_name: str, employer_name: str, test_link: str
    ) -> None:
        """Log a vacancy that has a test.

        Args:
            vacancy_name: Name of the vacancy.
            employer_name: Name of the employer.
            test_link: URL to the test.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {vacancy_name} - {employer_name} - {test_link}\n"

        with self._lock:
            self._rotate_if_needed()
            try:
                with self._file_path.open("a", encoding=self._encoding) as f:
                    f.write(line)
                logger.debug("Logged test vacancy: %s", vacancy_name)
            except Exception as ex:
                logger.error("Failed to log test vacancy: %s", ex)
                raise

    def _rotate_if_needed(self) -> None:
        """Rotate log file if it exceeds max size."""
        try:
            if (
                self._file_path.exists()
                and self._file_path.stat().st_size >= self._max_file_size
            ):
                self._rotate()
        except Exception:
            # If rotation fails, continue without rotation
            pass

    def _rotate(self) -> None:
        """Rotate log files."""
        for i in range(self._max_files - 1, 0, -1):
            src = self._file_path.with_suffix(f".{i}")
            dst = self._file_path.with_suffix(f".{i + 1}")
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)

        # Move current log to .1
        rotated = self._file_path.with_suffix(".1")
        if rotated.exists():
            rotated.unlink()
        self._file_path.rename(rotated)

    def read_logs(self, limit: int | None = None) -> list[str]:
        """Read log entries (most recent first).

        Args:
            limit: Maximum number of entries to return.

        Returns:
            List of log lines.
        """
        if not self._file_path.exists():
            return []

        with self._lock:
            try:
                with self._file_path.open("r", encoding=self._encoding) as f:
                    lines = f.readlines()
            except Exception as ex:
                logger.error("Failed to read log file: %s", ex)
                return []

        lines.reverse()  # Most recent first
        if limit:
            lines = lines[:limit]
        return [line.rstrip("\n") for line in lines]

    def clear(self) -> None:
        """Clear the log file."""
        with self._lock:
            try:
                if self._file_path.exists():
                    self._file_path.unlink()
                logger.debug("Log file cleared")
            except Exception as ex:
                logger.error("Failed to clear log file: %s", ex)
                raise
