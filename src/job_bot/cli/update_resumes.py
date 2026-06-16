"""CLI-операция ``update-resumes`` (VSA-rewrite issue #147).

Loads the user's resumes via the slice's vacancies port, filters them
by ``--search`` / ``--id``, and publishes every publishable one via
the same port's ``publish(resume_id)``.

The slice (with its vacancies port) is constructor-injected.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from job_bot.shared.utils.text import shorten

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _ResumeSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def vacancies(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``update-resumes``."""

    search: str | None
    id: str | None


class Operation(BaseOperation):
    """Обновить все резюме."""

    __aliases__ = ("update",)

    def __init__(self, slice_: _ResumeSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--search", help="Фильтр по названию резюме")
        parser.add_argument("--id", help="Фильтр по ID резюме")

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("update-resumes requires a slice with vacancies port")
            return 1

        items = list(slice_.vacancies.get_resumes() or [])
        slice_.vacancies.save_batch(items)

        for resume in items:
            resume_id = _field(resume, "id")
            resume_title = _field(resume, "title")
            resume_url = _field(resume, "alternate_url")
            can_publish = _field(resume, "can_publish_or_update", default=False)
            if args.id and resume_id != args.id:
                continue
            if (
                args.search
                and args.search.lower() not in (resume_title or "").lower()
            ):
                continue
            if not can_publish:
                logger.warning(f"Не могу обновить: {resume_url}")
                continue
            try:
                r = slice_.vacancies.publish(resume_id)
                assert {} == r
                print(
                    "✅ Обновлено",
                    resume_url,
                    "-",
                    shorten(resume_title or ""),
                )
            except Exception as ex:  # noqa: BLE001
                logger.error(f"Ошибка при обновлении резюме: {ex}")
        return 0


def _field(obj: Any, name: str, *, default: Any = None) -> Any:
    """Read ``name`` from ``obj`` as either dict key or attribute.

    The op accepts resumes in both shapes: Pydantic-like attribute
    objects (the live ``vacancies`` repo) and the raw JSON dict
    (legacy list-resumes payload).
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


__all__ = ("Operation", "Namespace")
