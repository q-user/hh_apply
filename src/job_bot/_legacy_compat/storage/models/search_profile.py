from __future__ import annotations

from typing import Any

from .base import BaseModel, mapped


class SearchProfileModel(BaseModel):
    """Сохранённый профиль поиска вакансий.

    Описывает одну конфигурацию поиска: какое резюме использовать, какие
    параметры передавать в HH API, какие правила релевантности применять и
    какой режим AI-фильтрации (``heavy`` / ``light`` / ``None``) использовать.

    `id` — строковый слаг (например, ``"django-senior"``), используется как
    PK и как ссылка из ``application_drafts.search_profile_id``.
    """

    id: str
    name: str
    resume_id: str
    enabled: bool = True
    search_params: Any = mapped(store_json=True, default=None)
    relevance_rules: Any = mapped(store_json=True, default=None)
    ai_filter_mode: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
