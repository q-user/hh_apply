# Этот модуль можно использовать как образец для других
from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from job_bot.shared.utils.text import shorten

from ..api import ApiError, datatypes
from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool


logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    pass


class Operation(BaseOperation):
    """Обновить все резюме"""

    __aliases__ = ["update"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--search", help="Фильтр по названию резюме")
        parser.add_argument("--id", help="Фильтр по ID резюме")

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        resumes: list[datatypes.Resume] = tool.get_resumes()
        # Там вызов API меняет поля
        tool.storage.resumes.save_batch(resumes)

        for resume in resumes:
            if args.id and resume["id"] != args.id:
                continue
            if (
                args.search
                and args.search.lower() not in resume["title"].lower()
            ):
                continue

            if not resume.get("can_publish_or_update"):
                logger.warning(f"Не могу обновить: {resume['alternate_url']}")
                continue
            try:
                r = tool.api_client.post(
                    f"/resumes/{resume['id']}/publish",
                )
                assert {} == r
                print(
                    "✅ Обновлено",
                    resume["alternate_url"],
                    "-",
                    shorten(resume["title"]),
                )
            except ApiError as ex:
                logger.error(f"Ошибка при обновлении резюме: {ex}")
