"""Cover letter domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass
class CoverLetter:
    """Cover letter entity."""

    id: str = field(default_factory=lambda: str(uuid4()))
    draft_id: str = ""
    content: str = ""
    status: str = "generated"  # generated, failed, pending
    template_used: str | None = None
    ai_generated: bool = False
    placeholders: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def create_draft(
        cls,
        draft_id: str,
        content: str,
        *,
        template_used: str | None = None,
        ai_generated: bool = False,
        placeholders: dict[str, Any] | None = None,
    ) -> CoverLetter:
        """Create a cover letter for an application draft."""
        return cls(
            draft_id=draft_id,
            content=content,
            status="generated" if content else "failed",
            template_used=template_used,
            ai_generated=ai_generated,
            placeholders=placeholders or {},
        )


@dataclass
class CoverLetterCreate:
    """Data for creating a new cover letter."""

    draft_id: str
    content: str
    status: str = "generated"
    template_used: str | None = None
    ai_generated: bool = False
    placeholders: dict[str, Any] = field(default_factory=dict)

    def to_cover_letter(self) -> CoverLetter:
        """Convert to CoverLetter entity."""
        return CoverLetter(
            draft_id=self.draft_id,
            content=self.content,
            status=self.status,
            template_used=self.template_used,
            ai_generated=self.ai_generated,
            placeholders=self.placeholders,
        )


# Default template (from original cover_letters.py)
DEFAULT_LETTER_TEMPLATE = (
    "{Здравствуйте|Добрый день}, меня зовут %(first_name)s. "
    "{Прошу|Предлагаю} рассмотреть {мою кандидатуру|мое резюме «%(resume_title)s»} "
    "на вакансию «%(vacancy_name)s». С уважением, %(first_name)s."
)
