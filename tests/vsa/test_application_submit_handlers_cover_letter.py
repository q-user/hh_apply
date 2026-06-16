"""Tests for submit-phase CoverLetterHandler (issue #145).

The handler is a thin adapter over the prep-phase
:class:`job_bot.application_prep.handlers.cover_letter_handler.CoverLetterHandler`.
The tests use a fake prep handler that records calls and returns
pre-baked letter text.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from job_bot.application_submit.handlers.cover_letter_handler import (
    CoverLetterHandler,
)


class _FakePrepCoverLetterHandler:
    """Fake prep-phase handler. Records every ``generate_cover_letter``
    call and returns a deterministic letter string."""

    def __init__(self, return_value: str = "fake letter") -> None:
        self._return = return_value
        self.calls: list[dict[str, Any]] = []

    def generate_cover_letter(
        self,
        vacancy: dict[str, Any],
        placeholders: dict[str, Any],
        *,
        resume_analysis: str = "",
        resume: dict[str, Any] | None = None,
        force: bool = False,
        required_by_vacancy: bool = False,
    ) -> str:
        self.calls.append(
            {
                "vacancy": vacancy,
                "placeholders": placeholders,
                "resume_analysis": resume_analysis,
                "resume": resume,
                "force": force,
                "required_by_vacancy": required_by_vacancy,
            }
        )
        return self._return


# ─── generate ─────────────────────────────────────────────────────────


class TestCoverLetterHandlerGenerate:
    """``generate`` is a thin pass-through to the prep handler."""

    def test_returns_prep_letter(self) -> None:
        prep = _FakePrepCoverLetterHandler(return_value="hello")
        handler = CoverLetterHandler(prep)
        out = handler.generate({"id": 1, "name": "V"}, {"first_name": "Иван"})
        assert out == "hello"

    def test_forwards_all_kwargs(self) -> None:
        """``force``, ``required_by_vacancy``, ``resume_analysis``, and
        ``resume`` are all forwarded verbatim."""
        prep = _FakePrepCoverLetterHandler()
        handler = CoverLetterHandler(prep)
        vacancy = {"id": 1, "name": "V"}
        placeholders = {"first_name": "Иван", "last_name": "Иванов"}
        resume = {"id": "r1", "title": "Backend"}
        handler.generate(
            vacancy,
            placeholders,
            resume_analysis="analysis",
            resume=resume,
            force=True,
            required_by_vacancy=True,
        )
        assert len(prep.calls) == 1
        call = prep.calls[0]
        assert call["vacancy"] is vacancy
        assert call["placeholders"] is placeholders
        assert call["resume_analysis"] == "analysis"
        assert call["resume"] is resume
        assert call["force"] is True
        assert call["required_by_vacancy"] is True

    def test_empty_letter(self) -> None:
        """The prep handler may return an empty string (no letter)."""
        prep = _FakePrepCoverLetterHandler(return_value="")
        handler = CoverLetterHandler(prep)
        out = handler.generate({"id": 1, "name": "V"}, {})
        assert out == ""


# ─── Protocol satisfaction ────────────────────────────────────────────


def test_cover_letter_handler_satisfies_cover_letter_port() -> None:
    from job_bot.application_submit.ports.cover_letter_port import (
        CoverLetterPort,
    )

    handler: CoverLetterPort = CoverLetterHandler(_FakePrepCoverLetterHandler())
    assert callable(handler.generate)
