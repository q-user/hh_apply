"""Application configuration paths (issue #158).

The legacy ``hh_applicant_tool.constants`` module's constants have
moved here. The 5-LOC stub at
``src/hh_applicant_tool/constants`` re-exports them for one release
window (removed in issue #158).
"""

from __future__ import annotations

from pathlib import Path

from job_bot.shared.utils._config_path import get_config_path

CONFIG_DIR: Path = get_config_path() / "hh-applicant-tool"
CONFIG_FILENAME: str = "config.json"
LOG_FILENAME: str = "log.txt"
DATABASE_FILENAME: str = "data"
COOKIES_FILENAME: str = "cookies.txt"
DESKTOP_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)


__all__ = [
    "CONFIG_DIR",
    "CONFIG_FILENAME",
    "LOG_FILENAME",
    "DATABASE_FILENAME",
    "COOKIES_FILENAME",
    "DESKTOP_USER_AGENT",
]
