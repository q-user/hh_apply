"""Vacancy search utilities (VSA).

Lives next to :mod:`job_bot.vacancy_search` (the VSA slice) and
contains helpers that used to live in the legacy
``hh_applicant_tool.services.vacancy_search`` shim
(issue #142 -- Phase D shim removal).

The :func:`build_search_params` builder mirrors the legacy
``build_search_params`` verbatim (only truthy values land in the
output dict, list values are passed as lists, bool values as
``"true"/"false"`` via :func:`bool2str`).
"""

from __future__ import annotations

from typing import Any

from job_bot.shared.utils.text import bool2str


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
    """Build query parameters for ``/vacancies`` or
    ``/resumes/{id}/similar_vacancies``.

    Mirrors the legacy ``hh_applicant_tool.services.vacancy_search.build_search_params``
    verbatim: only truthy values land in the output dict, list values
    are passed as lists, bool values as ``"true"/"false"`` via
    :func:`bool2str`.
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


__all__ = ["build_search_params"]
