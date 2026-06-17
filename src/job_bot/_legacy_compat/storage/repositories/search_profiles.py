from __future__ import annotations

from collections.abc import Iterator

from ..models.search_profile import SearchProfileModel
from .base import BaseRepository


class SearchProfilesRepository(BaseRepository):
    __table__ = "search_profiles"
    model = SearchProfileModel
    # PK — строковый слаг, нестандартный "id"
    pkey: str = "id"
    # UPSERT по id: prepare-vacancies может переписывать профиль
    # при пересохранении конфигурации.
    conflict_columns = ("id",)

    def find_enabled(self) -> Iterator[SearchProfileModel]:
        """Возвращает все профили с ``enabled=1``.

        Используется prepare-vacancies для итерации по активным конфигурациям.
        """
        yield from self.find(enabled=True)
