"""CLI-операция ``list-resumes`` (VSA-rewrite issue #147).

Loads the user's resumes from the slice's vacancies port (which owns
the ``/resumes/mine`` HTTP call + the local repository), persists the
batch via the same port's ``save_batch``, and prints a PrettyTable
with the resume id, title, and status.

The slice (with its vacancies port) is constructor-injected.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Protocol

from prettytable import PrettyTable

from job_bot.shared.utils.text import shorten

from ._base import BaseNamespace, BaseOperation

logger = logging.getLogger(__package__)


class _ResumeSlice(Protocol):
    """Minimal slice contract the op depends on."""

    @property
    def vacancies(self) -> Any: ...


class Namespace(BaseNamespace):
    """Аргументы ``list-resumes`` (no extra fields)."""


class Operation(BaseOperation):
    """Список резюме."""

    __aliases__ = ("ls-resumes", "resumes")

    def __init__(self, slice_: _ResumeSlice | None = None) -> None:
        self._slice = slice_

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace) -> int:
        slice_ = self._slice
        if slice_ is None:
            logger.error("list-resumes requires a slice with a vacancies port")
            return 1

        resumes = slice_.vacancies.get_resumes()
        items = list(resumes) if resumes is not None else []
        logger.debug("list-resumes: %d item(s)", len(items))
        slice_.vacancies.save_batch(items)

        t = PrettyTable(
            field_names=["ID", "Название", "Статус"], align="l", valign="t"
        )
        rows: list[list[Any]] = [
            [
                _field(x, "id"),
                shorten(_field(x, "title", default="") or ""),
                _get_status_name(x),
            ]
            for x in items
        ]
        t.add_rows(rows)
        print(t)
        return 0


def _field(obj: Any, name: str, *, default: Any = None) -> Any:
    """Read ``name`` from ``obj`` as either dict key or attribute."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _get_status_name(obj: Any) -> str:
    """Best-effort: read ``status.name`` from a dict or a status object."""
    status = _field(obj, "status")
    if status is None:
        return ""
    if isinstance(status, dict):
        name = status.get("name", "")
    else:
        name = getattr(status, "name", "")
    return (name or "").title()


__all__ = ("Operation", "Namespace")
