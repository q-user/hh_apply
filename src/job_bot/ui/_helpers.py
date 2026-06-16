"""Pure helpers for the ``job_bot.ui`` slice (VSA — Issue #150).

The :class:`Api` historically carried these helpers inline (see
``hh_applicant_tool.ui.api``).  Splitting them out keeps
:mod:`job_bot.ui.api` under the 500-LOC budget set by issue #150 and
makes the helpers independently testable.

Everything here is **pure** — no I/O, no service-locator, no
``HHApplicantTool`` references.  Two consumers:

* :func:`_build_command_from_params` — used by
  :meth:`Api.apply_vacancies` to normalise the UI payload into an
  :class:`ApplyToVacanciesCommand`.
* :func:`_mask_secrets` / :func:`_strip_masked` / :func:`_merge_config`
  — used by :meth:`Api.get_config` and :meth:`Api.save_config` to
  redact / restore sensitive values across the round-trip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hh_applicant_tool.application.dto import ApplyToVacanciesCommand


MASKED_KEYS = {"client_secret", "token"}
SENSITIVE_FIELD_NAMES = {
    "api_key",
    "password",
    "client_secret",
    "token",
    "proxy_url",
    "openai_proxy_url",
}
MASK_VALUE = "***"


def _mask_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: MASK_VALUE if k in SENSITIVE_FIELD_NAMES else _mask_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask_secrets(x) for x in obj]
    return obj


def _strip_masked(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_masked(v) for k, v in obj.items() if v != MASK_VALUE}
    if isinstance(obj, list):
        return [_strip_masked(x) for x in obj]
    return obj


def _merge_config(current: Any, updates: Any) -> Any:
    if isinstance(current, dict) and isinstance(updates, dict):
        merged = dict(current)
        for key, value in updates.items():
            merged[key] = _merge_config(current.get(key), value)
        return merged
    return updates


# Поисковые фильтры, которые собираются в ApplyToVacanciesCommand.search_params.
# ``search`` и ``order_by`` остаются top-level полями DTO.
_APPLY_SEARCH_PARAM_KEYS: tuple[str, ...] = (
    "schedule",
    "experience",
    "currency",
    "salary",
    "period",
    "date_from",
    "date_to",
    "top_lat",
    "bottom_lat",
    "left_lng",
    "right_lng",
    "sort_point_lat",
    "sort_point_lng",
    "search_field",
    "employment",
    "area",
    "metro",
    "professional_role",
    "industry",
    "employer_id",
    "excluded_employer_id",
    "label",
    "only_with_salary",
    "no_magic",
    "premium",
)

# Поля DTO, которые UI шлёт как массив (из multi-select / lookup-виджета).
_APPLY_LIST_KEYS: frozenset[str] = frozenset(
    {
        "employment",
        "area",
        "metro",
        "professional_role",
        "industry",
        "employer_id",
        "excluded_employer_id",
        "label",
        "search_field",
    }
)

# Поля DTO, которые UI шлёт как bool (checkbox).
_APPLY_BOOL_KEYS: frozenset[str] = frozenset(
    {
        "only_with_salary",
        "no_magic",
        "premium",
    }
)

# Поля DTO, которые UI шлёт как int.
_APPLY_INT_KEYS: frozenset[str] = frozenset(
    {
        "salary",
        "period",
    }
)

# Поля DTO, которые UI шлёт как float (геокоординаты).
_APPLY_FLOAT_KEYS: frozenset[str] = frozenset(
    {
        "top_lat",
        "bottom_lat",
        "left_lng",
        "right_lng",
        "sort_point_lat",
        "sort_point_lng",
    }
)


def _coerce_str(value: Any) -> str | None:
    if value is None or value is False or value == "":
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if value is None:
        return False
    return bool(value)


def _coerce_int(value: Any) -> int | None:
    if value is None or value is False or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None or value is False or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _coerce_list(value: Any) -> list[str] | None:
    """Нормализует значение массива из UI.

    Поддерживает:
    - ``list``/``tuple`` — элементы конвертируются в строки; ``dict`` →
      берётся ``id``; пустые элементы отбрасываются;
    - ``str`` вида ``"1,2,3"`` — разбивается по запятой;
    - одиночное значение — оборачивается в одноэлементный список.
    """
    if value is None or value is False or value == "":
        return None
    if isinstance(value, (list, tuple)):
        cleaned: list[str] = []
        for item in value:
            if item is None or item is False or item == "":
                continue
            if isinstance(item, dict):
                item_id = item.get("id")
                if item_id is None or item_id == "":
                    continue
                cleaned.append(str(item_id))
            else:
                cleaned.append(str(item))
        return cleaned or None
    if isinstance(value, str):
        parts = [s.strip() for s in value.split(",") if s.strip()]
        return parts or None
    return [str(value)]


def _build_command_from_params(
    params: dict[str, Any],
) -> "ApplyToVacanciesCommand":
    """Прямой маппинг UI-payload → :class:`ApplyToVacanciesCommand`.

    Без argparse / argv / Namespace: ключи из ``params`` напрямую
    кладутся в поля DTO с нормализацией типов. Неизвестные ключи
    (например, ``api_delay`` или ``max_responses``) тихо игнорируются —
    UI-payload может содержать служебные поля, которые обрабатываются
    выше (``api_delay``) или просто не поддерживаются текущей версией
    use case'а.
    """
    from hh_applicant_tool.application.dto import ApplyToVacanciesCommand

    p = dict(params)  # defensive copy

    search_params: dict[str, Any] = {}
    for key in _APPLY_SEARCH_PARAM_KEYS:
        if key not in p:
            continue
        value = p[key]
        if key in _APPLY_LIST_KEYS:
            coerced = _coerce_list(value)
        elif key in _APPLY_BOOL_KEYS:
            coerced = _coerce_bool(value)
        elif key in _APPLY_INT_KEYS:
            coerced = _coerce_int(value)
        elif key in _APPLY_FLOAT_KEYS:
            coerced = _coerce_float(value)
        else:
            coerced = _coerce_str(value)
        if coerced is None:
            continue
        # bool False и пустые строки отбрасываем.
        if coerced is False or coerced == "" or coerced == []:
            continue
        search_params[key] = coerced

    return ApplyToVacanciesCommand(
        resume_id=_coerce_str(p.get("resume_id")),
        search=_coerce_str(p.get("search")),
        search_params=search_params,
        per_page=_coerce_int(p.get("per_page")) or 100,
        total_pages=_coerce_int(p.get("total_pages")) or 20,
        dry_run=_coerce_bool(p.get("dry_run")),
        force_message=_coerce_bool(p.get("force_message")),
        use_ai=_coerce_bool(p.get("use_ai")),
        ai_filter=_coerce_str(p.get("ai_filter")),
        ai_rate_limit=_coerce_int(p.get("ai_rate_limit")) or 40,
        skip_tests=_coerce_bool(p.get("skip_tests")),
        send_email=_coerce_bool(p.get("send_email")),
        excluded_filter=_coerce_str(p.get("excluded_filter")),
        system_prompt=_coerce_str(p.get("system_prompt")) or "",
        message_prompt=_coerce_str(p.get("message_prompt")) or "",
        letter_file_content=_coerce_str(p.get("letter_file")),
        order_by=_coerce_str(p.get("order_by")),
        relevance_rules=p.get("relevance_rules"),
        max_responses=_coerce_int(p.get("max_responses")),
    )


__all__ = [
    "MASKED_KEYS",
    "MASK_VALUE",
    "SENSITIVE_FIELD_NAMES",
    "_build_command_from_params",
    "_coerce_bool",
    "_coerce_float",
    "_coerce_int",
    "_coerce_list",
    "_coerce_str",
    "_mask_secrets",
    "_merge_config",
    "_strip_masked",
]
