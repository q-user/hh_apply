"""Поиск вакансий через HH API (DEPRECATED).

Извлечено из ``operations/apply_vacancies.py`` (issue #3). Сервис инкапсулирует:
- построение query-параметров ``/vacancies`` / ``/resumes/{id}/similar_vacancies``;
- пагинацию и выбор эндпоинта по наличию ``text`` (поиск) или его отсутствию
  (рекомендации).

Зависимости (api_client, per_page, total_pages) передаются явно в конструкторе,
что упрощает юнит-тестирование и переиспользование в ``prepare-vacancies``.

.. deprecated:: 1.0
   Use ``job_bot.vacancy_search.VacancySearchSlice`` instead.
   This service is **planned for removal in version 2.0** (VSA switchover).
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Iterator, Mapping
from typing import Any

from ..api.datatypes import PaginatedItems, SearchVacancy
from ..utils.string import bool2str

logger = logging.getLogger(__package__)

warnings.warn(
    "hh_applicant_tool.services.vacancy_search is deprecated; use job_bot.vacancy_search instead (issue #53).",
    DeprecationWarning,
    stacklevel=2,
)


def build_search_params(
    *,
    page: int,
    per_page: int,
    order_by: str | None = None,
    text: str | None = None,
    schedule: str | None = None,
    experience: str | None = None,
    currency: str | None = None,
    salary: int | None = None,
    period: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_lat: float | None = None,
    bottom_lat: float | None = None,
    left_lng: float | None = None,
    right_lng: float | None = None,
    sort_point_lat: float | None = None,
    sort_point_lng: float | None = None,
    search_field: list[str] | None = None,
    employment: list[str] | None = None,
    area: list[str] | None = None,
    metro: list[str] | None = None,
    professional_role: list[str] | None = None,
    industry: list[str] | None = None,
    employer_id: list[str] | None = None,
    excluded_employer_id: list[str] | None = None,
    label: list[str] | None = None,
    only_with_salary: bool = False,
    no_magic: bool = False,
    premium: bool = False,
) -> dict[str, Any]:
    """Собирает query-параметры для ``/vacancies`` или
    ``/resumes/{id}/similar_vacancies``.

    Поведение зеркалирует ``Operation._get_search_params`` в
    ``apply_vacancies.py`` (issue #2). Только truthy-значения попадают в
    словарь; list-значения передаются как list, bool-значения — как
    ``"true"/"false"`` через ``bool2str``.

    Параметры берутся keyword-only, чтобы порядок не играл роли и сигнатура
    была читаемой при вызове из ``apply_vacancies.py`` и из будущего
    ``prepare-vacancies``.
    """
    params: dict[str, Any] = {
        "page": page,
        "per_page": per_page,
    }
    if order_by:
        params["order_by"] = order_by
    if text:
        params["text"] = text
    if schedule:
        params["schedule"] = schedule
    if experience:
        params["experience"] = experience
    if currency:
        params["currency"] = currency
    if salary:
        params["salary"] = salary
    if period:
        params["period"] = period
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if top_lat:
        params["top_lat"] = top_lat
    if bottom_lat:
        params["bottom_lat"] = bottom_lat
    if left_lng:
        params["left_lng"] = left_lng
    if right_lng:
        params["right_lng"] = right_lng
    if sort_point_lat:
        params["sort_point_lat"] = sort_point_lat
    if sort_point_lng:
        params["sort_point_lng"] = sort_point_lng
    if search_field:
        params["search_field"] = list(search_field)
    if employment:
        params["employment"] = list(employment)
    if area:
        params["area"] = list(area)
    if metro:
        params["metro"] = list(metro)
    if professional_role:
        params["professional_role"] = list(professional_role)
    if industry:
        params["industry"] = list(industry)
    if employer_id:
        params["employer_id"] = list(employer_id)
    if excluded_employer_id:
        params["excluded_employer_id"] = list(excluded_employer_id)
    if label:
        params["label"] = list(label)
    if only_with_salary:
        params["only_with_salary"] = bool2str(only_with_salary)
    if no_magic:
        params["no_magic"] = bool2str(no_magic)
    if premium:
        params["premium"] = bool2str(premium)
    return params


class VacancySearchService:
    """Сервис поиска вакансий через HH API.

    Attributes:
        api_client: экземпляр ``ApiClient`` (см. ``api/client.py``).
        per_page: сколько вакансий запрашивать на странице.
        total_pages: верхняя граница числа страниц (для защиты от зацикливания).
    """

    def __init__(self, api_client: Any, *, per_page: int, total_pages: int):
        self.api_client = api_client
        self.per_page = per_page
        self.total_pages = total_pages

    def search(
        self,
        search_params: Mapping[str, Any],
        *,
        resume_id: str | None = None,
    ) -> Iterator[SearchVacancy]:
        """Итерирует вакансии по всем страницам.

        Если в ``search_params`` передан ``text`` — используется
        ``/vacancies`` (поиск по ключевым словам). Иначе — рекомендации
        ``/resumes/{resume_id}/similar_vacancies`` (для этого ``resume_id``
        обязателен).

        ``search_params`` ожидается уже сформированным (через
        :func:`build_search_params` или прямую маппинг из
        ``SearchProfileModel.search_params``); ``page`` и ``per_page``
        сервис подмешивает сам.
        """
        has_text = bool(search_params.get("text"))

        for page in range(self.total_pages):
            logger.debug("Загружаем вакансии со страницы: %d", page + 1)
            params = dict(search_params)
            params["page"] = page
            params["per_page"] = self.per_page

            if has_text:
                endpoint = "/vacancies"
            else:
                if not resume_id:
                    raise ValueError(
                        "resume_id is required for similar_vacancies endpoint"
                    )
                endpoint = f"/resumes/{resume_id}/similar_vacancies"

            res: PaginatedItems[SearchVacancy] = self.api_client.get(
                endpoint, params
            )
            logger.debug("Количество вакансий: %s", res.get("found"))

            items = res.get("items") or []
            if not items:
                return

            yield from items

            if page >= res.get("pages", 0) - 1:
                return
