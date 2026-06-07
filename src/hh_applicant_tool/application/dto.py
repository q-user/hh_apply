"""Data Transfer Objects для application layer.

DTO — единственные структуры данных, которыми обмениваются
представления (CLI, UI, worker) и use case'ы. Они не зависят ни от
argparse, ни от ``HHApplicantTool``, ни от конкретного транспорта —
это «чистые» python-объекты.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ApplyToVacanciesCommand:
    """Входные данные для :class:`ApplyToVacanciesUseCase`.

    Формируется одинаково из CLI (``operations/apply_vacancies.py``),
    из UI (``ui/api.py``) и из воркера/бота — без привязки к argparse.

    Attributes:
        resume_id: фильтр по конкретному резюме (``--resume-id``).
        search: поисковая строка (``--search``). Если ``None`` —
            идём через ``/resumes/{id}/similar_vacancies``.
        search_params: плоский dict с search-фильтрами
            (``area``, ``metro``, ``schedule`` и т.п.). Эти же kwargs
            принимает :func:`hh_applicant_tool.services.build_search_params`.
        per_page: количество вакансий на странице (``--per-page``).
        total_pages: верхняя граница страниц (``--total-pages``).
        dry_run: не отправлять отклики (``--dry-run``).
        force_message: всегда генерировать сопроводительное письмо.
        use_ai: использовать AI для генерации писем.
        ai_filter: режим AI-фильтра вакансий (``heavy`` / ``light``).
        ai_rate_limit: лимит запросов к AI в минуту.
        skip_tests: пропускать вакансии с тестами (``--skip-tests``).
        send_email: отправлять письмо работодателю (``--send-email``).
        excluded_filter: regex для исключения вакансий
            (``--excluded-filter``).
        system_prompt: system_prompt для AI-генерации писем.
        message_prompt: prompt для AI-генерации писем.
        letter_file_content: содержимое файла ``--letter-file``
            (если указан). Используется как шаблон сопроводительного
            письма вместо дефолтного.
        order_by: сортировка вакансий (``--order-by``).
    """

    resume_id: str | None = None
    search: str | None = None
    search_params: dict[str, Any] = field(default_factory=dict)
    per_page: int = 100
    total_pages: int = 20
    dry_run: bool = False
    force_message: bool = False
    use_ai: bool = False
    ai_filter: Literal["heavy", "light"] | None = None
    ai_rate_limit: int = 40
    skip_tests: bool = False
    send_email: bool = False
    excluded_filter: str | None = None
    system_prompt: str = ""
    message_prompt: str = ""
    letter_file_content: str | None = None
    order_by: str | None = None


@dataclass
class ApplyToVacanciesResult:
    """Статистика выполнения :class:`ApplyToVacanciesUseCase`.

    Attributes:
        resumes_processed: количество обработанных резюме.
        vacancies_seen: сколько вакансий увидели (до фильтров).
        skipped: сколько вакансий пропустили (по любой причине).
        applied: сколько откликов реально отправлено.
        failed: сколько попыток отправки упало с ошибкой.
        limit_reached: был ли достигнут дневной лимит hh.ru.
    """

    resumes_processed: int = 0
    vacancies_seen: int = 0
    skipped: int = 0
    applied: int = 0
    failed: int = 0
    limit_reached: bool = False
