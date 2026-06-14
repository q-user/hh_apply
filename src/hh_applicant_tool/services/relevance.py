"""AI-фильтрация вакансий по релевантности.

.. deprecated:: 1.8
   Use :class:`job_bot.application_prep.handlers.RelevanceHandler`
   (or :attr:`job_bot.application_prep.slice.ApplicationPrepSlice.relevance`)
   instead. This module is part of the VSA switchover (issue #54) and
   **planned for removal in version 2.0**. New code should depend on
   the new slice; this shim is kept for backward compatibility only.

Извлечено из ``operations/apply_vacancies.py`` (issue #3). Сервис инкапсулирует
две стратегии:

- **heavy** — глубокий анализ (полное описание + опыт кандидата).
- **light** — быстрый матч по названию + skill_set.

Возвращает структурированный :class:`RelevanceResult`
(``suitable`` / ``relevance_score`` / ``success_probability`` /
``primary_stack`` / ``risks`` / ``reason`` и др.), который сохраняется в
``application_drafts.analysis_json``, ``relevance_score``,
``success_probability`` и ``relevance_reason`` (issue #4).

Контракт AI-ответа — строгий JSON. ``relevance_rules`` из
:class:`SearchProfileModel` участвуют в построении system prompt и
применяются к результату (issue #4): ``min_score`` понижает
``suitable`` ниже порога, ``reject_if_primary`` отклоняет вакансию,
если её стек попадает в «запрещённый» список.
"""

from __future__ import annotations

import json
import logging
import re
import warnings
from dataclasses import dataclass, field
from typing import Any

import requests

from ..ai.base import AIError
from ..api import BadResponse
from ..api.errors import ApiError
from ..utils.string import strip_tags

logger = logging.getLogger(__package__)

# Issue #54: RelevanceService is deprecated. The deprecation warning
# is emitted on instantiation (not at import time) so that just
# importing the module for re-exports doesn't pollute every test run.

# Максимум попыток переспросить AI, если JSON невалидный
MAX_RETRIES = 3

# Границы для 0..100 score
SCORE_MIN = 0
SCORE_MAX = 100


@dataclass
class RelevanceResult:
    """Структурированный результат AI-фильтра (issue #4).

    Содержит все поля, которые AI может вернуть в новом «строгом» JSON
    контракте, плюс обратную совместимость со старым boolean-only
    форматом (``{"suitable": true/false}``).

    Attributes:
        suitable: итоговое решение — подходит ли кандидат.
        relevance_score: числовая оценка релевантности (0..100).
        success_probability: оценка шансов на оффер (0..100).
        primary_stack: основные технологии вакансии.
        secondary_stack: второстепенные технологии вакансии.
        project_summary: краткое описание проекта/продукта.
        complexity: сложность задач (``low``/``medium``/``high``).
        salary_summary: краткое описание зарплаты.
        employment_format: формат занятости (remote/office/гибрид).
        perks: плюшки (remote, white salary, и т.п.).
        risks: риски/опасения по вакансии.
        reason: текстовое обоснование от AI.
        raw_response: исходный ответ AI (для отладки и
            ``application_drafts.analysis_json``).
    """

    suitable: bool
    relevance_score: int | None = None
    success_probability: int | None = None
    primary_stack: list[str] | None = None
    secondary_stack: list[str] | None = None
    project_summary: str | None = None
    complexity: str | None = None
    salary_summary: str | None = None
    employment_format: str | None = None
    perks: list[str] | None = None
    risks: list[str] | None = None
    reason: str | None = None
    raw_response: str | None = None
    # Применённые правила профиля — нужно для дебага и логирования.
    # Не пишется в ``to_analysis_json`` (это профиль-локальные метаданные).
    applied_rules: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def score(self) -> int | None:
        """Backwards-compat alias для ``relevance_score`` (issue #3)."""
        return self.relevance_score

    def to_analysis_json(self) -> dict[str, Any]:
        """Возвращает dict, пригодный для записи в
        ``application_drafts.analysis_json``.

        ``None``-поля отбрасываются, чтобы не раздувать JSON.
        ``raw_response`` намеренно НЕ включается (он свой столбец/
        используется отдельно для дебага).
        """
        data: dict[str, Any] = {"suitable": self.suitable}
        for key in (
            "relevance_score",
            "success_probability",
            "primary_stack",
            "secondary_stack",
            "project_summary",
            "complexity",
            "salary_summary",
            "employment_format",
            "perks",
            "risks",
            "reason",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


# ─── helpers для нормализации ответа AI ───────────────────────────


def _as_int_0_100(value: Any) -> int | None:
    """Приводит ``value`` к ``int`` в диапазоне 0..100.

    Возвращает ``None`` для нечисловых значений, пустых строк, ``None``,
    и для значений, которые не получается привести к ``int``.
    Округляет float (``int(80.7) == 80``), клампит диапазон.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # bool — подкласс int, но семантически это не число;
        # ``True``/``False`` для score бессмысленно.
        return None
    if isinstance(value, int):
        return max(SCORE_MIN, min(SCORE_MAX, value))
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return max(SCORE_MIN, min(SCORE_MAX, int(value)))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return _as_int_0_100(float(s))
        except (TypeError, ValueError):
            return None
    return None


def _as_str_list(value: Any) -> list[str] | None:
    """Приводит ``value`` к ``list[str]`` (``None`` если пусто/не list).

    Не-payload элементы молча приводятся через ``str()`` и фильтруются
    от пустых строк — это устойчиво к типичным ответам AI вроде
    ``["Django", 1, null, "PostgreSQL"]``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # AI иногда отдаёт строку вместо массива — берём её как один элемент,
        # если она непустая.
        v = value.strip()
        return [v] if v else None
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        if item is None:
            continue
        s = str(item).strip()
        if s:
            out.append(s)
    return out or None


# ─── парсинг ответа AI ────────────────────────────────────────────


# Регексп для fallback-поиска JSON-блока с ``"suitable"`` внутри текста.
# Используется, если AI обернул JSON в пояснение и не снял markdown fence.
_FALLBACK_JSON_RE = re.compile(
    r"\{[^{}]*\"suitable\"\s*:\s*(?:true|false)[^{}]*\}",
    re.IGNORECASE,
)


def _strip_markdown_fence(text: str) -> str:
    """Снимает ```` ```json ... ```` обрамляющие блоки, не повреждая JSON.

    Удаляет ТОЛЬКО обрамляющие fence (``\`\`\`json`` и ``\`\`\``), если они
    стоят в начале/конце текста. Внутренние ``\`\`\`` не трогаем.
    """
    s = text.strip()
    if s.startswith("```"):
        # Срезаем первую строку (``\`\`\`json`` / ``\`\`\```)
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        else:
            s = s.lstrip("`")
    if s.endswith("```"):
        s = s[:-3].rstrip()
    return s


def parse_ai_json_response(response: str) -> RelevanceResult | None:
    """Парсит ответ AI в :class:`RelevanceResult`.

    Поддерживаемые формы ответа (issue #4):

    - ``"да"/"yes"/"true"`` → ``RelevanceResult(suitable=True)``;
    - ``"нет"/"no"/"false"`` → ``RelevanceResult(suitable=False)``;
    - JSON ``{"suitable": bool, ...}`` — произвольный набор полей из
      расширенного контракта (``relevance_score``, ``primary_stack``,
      ``risks`` и т.д.). Поле ``score`` (legacy) маппится в
      ``relevance_score``;
    - Тот же JSON, обёрнутый в markdown fence ``\`\`\`json ... \`\`\``;
    - Тот же JSON, вкрапленный в произвольный текст (fallback-регексп).

    Если ни одна форма не сработала — возвращает ``None`` (для retry в
    :meth:`RelevanceService._ask_ai_suitability`).
    """
    if response is None:
        return None
    text = str(response).strip()
    if not text:
        return None

    # Boolean-only ответ (case-insensitive). По спеке score не заполняется.
    lower = text.lower()
    if lower in ("да", "yes", "true"):
        return RelevanceResult(suitable=True, raw_response=text)
    if lower in ("нет", "no", "false"):
        return RelevanceResult(suitable=False, raw_response=text)

    clean = _strip_markdown_fence(text)
    if clean:
        try:
            data = json.loads(clean)
            if isinstance(data, dict) and "suitable" in data:
                return _result_from_dict(data, text)
        except (ValueError, TypeError) as ex:
            logger.debug("JSON parse error: %s. Raw response: %s", ex, text)

    # Fallback: ищем JSON-блок с "suitable" в произвольном тексте.
    json_match = _FALLBACK_JSON_RE.search(text)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if isinstance(data, dict):
                return _result_from_dict(data, text)
        except (ValueError, TypeError):
            pass

    return None


def _result_from_dict(data: dict, raw: str) -> RelevanceResult:
    """Собирает :class:`RelevanceResult` из dict ответа AI.

    Legacy-поле ``score`` трактуется как alias ``relevance_score``
    (issue #4). При наличии обоих полей предпочтение у ``relevance_score``.
    """
    suitable = bool(data.get("suitable"))

    # legacy ``score`` → ``relevance_score``
    relevance_score = _as_int_0_100(data.get("relevance_score"))
    if relevance_score is None:
        relevance_score = _as_int_0_100(data.get("score"))

    return RelevanceResult(
        suitable=suitable,
        relevance_score=relevance_score,
        success_probability=_as_int_0_100(data.get("success_probability")),
        primary_stack=_as_str_list(data.get("primary_stack")),
        secondary_stack=_as_str_list(data.get("secondary_stack")),
        project_summary=_as_optional_str(data.get("project_summary")),
        complexity=_as_optional_str(data.get("complexity")),
        salary_summary=_as_optional_str(data.get("salary_summary")),
        employment_format=_as_optional_str(data.get("employment_format")),
        perks=_as_str_list(data.get("perks")),
        risks=_as_str_list(data.get("risks")),
        reason=_as_optional_str(data.get("reason")),
        raw_response=raw,
    )


def _as_optional_str(value: Any) -> str | None:
    """Возвращает ``str(value)`` или ``None`` для пустых значений."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# ─── формат relevance_rules для system prompt ─────────────────────


def _format_relevance_rules(rules: dict[str, Any] | None) -> str:
    """Рендерит :attr:`SearchProfileModel.relevance_rules` в текст для
    вставки в system prompt.

    Возвращает пустую строку, если правил нет или они пустые.
    """
    if not rules:
        return ""
    parts: list[str] = []
    must = _as_str_list(rules.get("must_have"))
    if must:
        parts.append(
            f"- Обязательные технологии (must have): {', '.join(must)}"
        )
    nice = _as_str_list(rules.get("nice_to_have"))
    if nice:
        parts.append(
            f"- Желательные технологии (nice to have): {', '.join(nice)}"
        )
    allowed = _as_str_list(rules.get("allowed_secondary"))
    if allowed:
        parts.append(
            "- Допустимые второстепенные технологии "
            "(НЕ отклоняй вакансию, если они в secondary_stack): "
            f"{', '.join(allowed)}"
        )
    reject = _as_str_list(rules.get("reject_if_primary"))
    if reject:
        parts.append(
            "- Категорически отклоняй (suitable=false), если в primary_stack "
            f"есть: {', '.join(reject)}"
        )
    role = _as_optional_str(rules.get("strict_role"))
    if role:
        parts.append(f"- Целевая роль кандидата: {role}")
    if not parts:
        return ""
    return (
        "\n\n#### ПРАВИЛА РЕЛЕВАНТНОСТИ (ОБЯЗАТЕЛЬНО УЧИТЫВАЙ)\n"
        + "\n".join(parts)
    )


# ─── system prompts ───────────────────────────────────────────────


def build_filter_system_prompt_heavy(
    resume_analysis: str,
    *,
    relevance_rules: dict[str, Any] | None = None,
) -> str:
    """System prompt для тяжёлого AI-фильтра (issue #4).

    Требует от AI СТРОГО JSON следующей формы::

        {
          "suitable": true,
          "relevance_score": 92,
          "success_probability": 78,
          "primary_stack": ["Python", "Django"],
          "secondary_stack": ["FastAPI"],
          "project_summary": "...",
          "complexity": "low|medium|high",
          "salary_summary": "...",
          "employment_format": "...",
          "perks": ["..."],
          "risks": ["..."],
          "reason": "..."
        }

    ``relevance_rules`` (опционально) — ``relevance_rules`` из
    :class:`SearchProfileModel` — дописываются отдельной секцией.
    """
    return f"""
Ты - HR-эксперт и карьерный консультант с 15-летним опытом IT-подбора.
Твоя задача - объективно решить, подходит ли кандидат под данную вакансию.

---

#### ВХОДНЫЕ ДАННЫЕ (INPUT)
Для анализа тебе предоставлены:
1. [JOB] - Описание вакансии (стек, задачи, компания).
2. [CANDIDATE] - Полные данные из резюме соискателя.

#### ЗАДАНИЕ (TASK)
1. Выдели из блока [CANDIDATE] ключевой технологический стек, профессиональную роль и главные достижения (ищи цифры, метрики, конкретные результаты).
2. Проанализируй [JOB] и определи основные боли работодателя и требуемый уровень экспертности.
3. Сравни эти данные. Решение "ПОДХОДИТ" (true) принимай только в том случае, если опыт и достижения кандидата позволяют эффективно решать задачи, описанные в [JOB].

Принимай решение взвешенно, как при реальном найме на Senior/Lead позиции.
{_format_relevance_rules(relevance_rules)}

#### ВЫХОД (OUTPUT)
Ответ СТРОГО в формате JSON (без markdown-обрамления, без пояснений):
{{
  "suitable": true,
  "relevance_score": 92,
  "success_probability": 78,
  "primary_stack": ["Python", "Django"],
  "secondary_stack": ["FastAPI"],
  "project_summary": "краткое описание проекта/продукта",
  "complexity": "low|medium|high",
  "salary_summary": "вилка и валюта одной строкой",
  "employment_format": "remote/office/hybrid, full-time/part-time",
  "perks": ["remote", "white salary"],
  "risks": ["что может смутить соискателя"],
  "reason": "краткое профессиональное обоснование: какие именно навыки/достижения кандидата мэтчатся с задачами вакансии"
}}

Правила:
- "suitable" — финальное решение, true/false.
- "relevance_score" — целое 0..100, насколько вакансия релевантна кандидату.
- "success_probability" — целое 0..100, шансы получить оффер.
- "primary_stack" / "secondary_stack" — массивы строк-технологий.
- "complexity" — одно из: "low", "medium", "high".
- Все строковые поля могут быть null, если данных недостаточно.

---

### [CANDIDATE DATA]
{resume_analysis}
"""


def build_filter_system_prompt_light(
    resume_analysis: str,
    *,
    relevance_rules: dict[str, Any] | None = None,
) -> str:
    """System prompt для лёгкого AI-фильтра (issue #4).

    Требует укороченный JSON — те же ключевые поля, но без подробностей::

        {
          "suitable": true,
          "relevance_score": 80,
          "primary_stack": ["Python", "Django"],
          "secondary_stack": [],
          "risks": [],
          "reason": "..."
        }
    """
    return f"""
Ты делаешь очень грубую проверку: подходит вакансия или нет.

Используй только:
- название резюме
- список навыков резюме
- название вакансии
- явно указанные ключевые навыки вакансии

Не анализируй описание, обязанности, контекст, домен, уровень, карьерный рост и прочую воду.
Не додумывай ничего, чего нет в тексте.

Правила:
- если название вакансии и резюме в одной профессии или близких ролях, и есть хотя бы частичное совпадение по ключевым навыкам -> suitable = true
- если роли явно разные или совпадений по навыкам почти нет -> suitable = false
- если данных мало -> ориентируйся только на явные совпадения, без фантазий
{_format_relevance_rules(relevance_rules)}

Ответ СТРОГО в формате JSON (без markdown-обрамления, без пояснений):
{{
  "suitable": true,
  "relevance_score": 80,
  "primary_stack": ["Python", "Django"],
  "secondary_stack": [],
  "risks": [],
  "reason": "краткое обоснование"
}}

Кандидат:
{resume_analysis}
"""


# ─── применение relevance_rules к результату AI ───────────────────


def _apply_relevance_rules(
    result: RelevanceResult,
    rules: dict[str, Any] | None,
) -> RelevanceResult:
    """Применяет ``relevance_rules`` к результату AI (issue #4).

    Изменяет ``result`` **in place** и возвращает тот же объект.

    Применяемые правила:

    - ``min_score`` — если ``relevance_score < min_score``, то
      ``suitable=False`` (с указанием причины в ``reason``).
    - ``reject_if_primary`` — если в ``primary_stack`` есть элемент из
      этого списка, то ``suitable=False``.

    ``allowed_secondary`` / ``nice_to_have`` / ``must_have`` / ``strict_role``
    сюда НЕ входят — они сообщаются AI в system prompt.
    """
    if not rules:
        return result
    applied: dict[str, Any] = {}

    min_score = _as_int_0_100(rules.get("min_score"))
    if min_score is not None and result.relevance_score is not None:
        if result.relevance_score < min_score:
            applied["min_score"] = min_score
            result.suitable = False
            note = (
                f"relevance_score={result.relevance_score} ниже "
                f"min_score={min_score}"
            )
            result.reason = _append_reason(result.reason, note)

    reject = _as_str_list(rules.get("reject_if_primary"))
    primary = result.primary_stack or []
    if reject and primary:
        # Case-insensitive матч по строковому совпадению.
        # AI может вернуть "FastAPI" и правило "fastapi" — должны совпасть.
        reject_lower = {r.lower() for r in reject}
        matched = [p for p in primary if p.lower() in reject_lower]
        if matched:
            applied["reject_if_primary_matched"] = matched
            result.suitable = False
            note = (
                f"primary_stack содержит запрещённые технологии: "
                f"{', '.join(matched)}"
            )
            result.reason = _append_reason(result.reason, note)

    if applied:
        result.applied_rules = applied
    return result


def _append_reason(existing: str | None, addition: str) -> str:
    """Дописывает ``addition`` в ``existing`` через "; " (issue #4)."""
    if not existing:
        return addition
    if addition in existing:
        return existing
    return f"{existing}; {addition}"


# ─── основной сервис ──────────────────────────────────────────────


class RelevanceService:
    """AI-фильтр вакансий (heavy/light).

    Принимает уже сконфигурированный ``ai_client`` с установленным
    ``system_prompt`` (через ``get_vacancy_filter_ai(prompt)`` или
    напрямую). Это позволяет переиспользовать AI-клиент в других местах.

    Attributes:
        api_client: HH API клиент.
        ai_client: экземпляр ``ChatOpenAI`` с system_prompt или ``None``
            (тогда фильтрация отключена — все вакансии считаются
            подходящими).
        relevance_rules: правила релевантности из
            :class:`SearchProfileModel.relevance_rules` или ``None``.
            Влияют и на system prompt, и на пост-обработку результата
            (issue #4).
        ai_failure_mode: режим при сбое AI (issue #28):
            ``"permissive"`` (по умолчанию) — вакансия считается подходящей;
            ``"strict"`` — вакансия отклоняется;
            ``"raise"`` — исключение пробрасывается наверх.
        vacancy_fetcher: порт для загрузки описания вакансии (issue #33).
            Если не задан — используется ``api_client.get()`` напрямую.
    """

    def __init__(
        self,
        api_client: Any,
        ai_client: Any = None,
        *,
        relevance_rules: dict[str, Any] | None = None,
        ai_failure_mode: str = "permissive",
        vacancy_fetcher: Any = None,
    ):
        warnings.warn(
            "hh_applicant_tool.services.relevance is deprecated; use job_bot.application_prep instead (issue #54).",
            DeprecationWarning,
            stacklevel=2,
        )
        if ai_failure_mode not in ("permissive", "strict", "raise"):
            raise ValueError(
                f"ai_failure_mode must be 'permissive', 'strict', or 'raise', "
                f"got {ai_failure_mode!r}"
            )
        self.api_client = api_client
        self.ai_client = ai_client
        self.relevance_rules = relevance_rules
        self._ai_failure_mode = ai_failure_mode
        self._vacancy_fetcher = vacancy_fetcher
        # Кеш для тяжёлого анализа резюме
        self._resume_analysis_cache: dict[tuple[str | None, str], str] = {}

    # ─── Анализ резюме (с кешем) ─────────────────────────────────

    def analyze_resume_heavy(self, resume: dict[str, Any]) -> str:
        """Тяжёлый анализ резюме (полный текст + опыт). Результат кешируется."""
        resume_id = resume.get("id")
        cache_key = (resume_id, "heavy")
        if cache_key in self._resume_analysis_cache:
            return self._resume_analysis_cache[cache_key]

        if resume_id:
            try:
                full_resume = self.api_client.get(f"/resumes/{resume_id}")
            except Exception as ex:  # noqa: BLE001
                # Deprecated RelevanceService shim — return empty analysis
                # on any failure so the caller can fall back to a lighter pass.
                logger.warning("Не удалось получить полное резюме: %s", ex)
                return ""

            parts: list[str] = []

            title = full_resume.get("title", "")
            if title:
                parts.append(f"Должность: {title}")

            if "skills" in full_resume:
                parts.append("\n---------- О СЕБЕ ----------")
                parts.append(full_resume.get("skills", ""))

            if "skill_set" in full_resume and full_resume["skill_set"]:
                parts.append("\n---------- НАВЫКИ ----------")
                skills_row = ", ".join(full_resume["skill_set"])
                parts.append(skills_row)

            if "experience" in full_resume:
                parts.append("\n---------- ОПЫТ РАБОТЫ ----------")
                for exp in full_resume.get("experience", []):
                    company = exp.get("company", "Не указано")
                    position = exp.get("position", "Не указано")
                    start = exp.get("start", "")
                    end = exp.get("end") or "по настоящее время"
                    parts.append(f"\n- {company}")
                    parts.append(f" Должность: {position}")
                    parts.append(f" Период: {start} - {end}")
                    description = exp.get("description")
                    if description:
                        parts.append(" Описание:")
                        parts.append(f" {description}")

            result = "\n".join(parts)
            self._resume_analysis_cache[cache_key] = result
            return result

        return ""

    def analyze_resume_light(self, resume: dict[str, Any]) -> str:
        """Лёгкий анализ резюме (только title + skill_set). Результат кешируется."""
        resume_id = resume.get("id")
        cache_key = (resume_id, "light")
        if cache_key in self._resume_analysis_cache:
            return self._resume_analysis_cache[cache_key]

        try:
            full_resume = self.api_client.get(f"/resumes/{resume_id}")
        except Exception as ex:  # noqa: BLE001
            # Deprecated RelevanceService shim — see comment above.
            logger.warning("Не удалось получить полное резюме: %s", ex)
            return ""

        parts: list[str] = []
        title = full_resume.get("title", "")
        if title:
            parts.append(f"Должность: {title}")
        if "skill_set" in full_resume and full_resume["skill_set"]:
            parts.append("Навыки: ")
            skills_row = ", ".join(full_resume["skill_set"])
            parts.append(skills_row)

        result = "\n".join(parts)
        self._resume_analysis_cache[cache_key] = result
        return result

    # ─── Контекст вакансии (для промпта) ───────────────────────────

    def get_vacancy_key_skills(self, vacancy_id: str | int) -> str:
        """Возвращает key_skills вакансии одной строкой."""
        try:
            full_vacancy = self.api_client.get(f"/vacancies/{vacancy_id}")
            key_skills_data = full_vacancy.get("key_skills") or []
            return ", ".join(
                s["name"] for s in key_skills_data if s.get("name")
            )
        except Exception as ex:  # noqa: BLE001
            # Deprecated RelevanceService shim — key_skills is optional context;
            # an empty string is the safe fallback for any failure.
            logger.warning(
                "Не удалось получить key_skills вакансии %s: %s",
                vacancy_id,
                ex,
            )
            return ""

    def build_vacancy_context(
        self,
        vacancy: dict[str, Any],
        *,
        full_vacancy: dict[str, Any] | None = None,
        include_full: bool = False,
    ) -> str:
        """Строит текстовое описание вакансии для промпта.

        ``include_full=True`` (heavy) — вставляет описание из ``full_vacancy``.
        ``include_full=False`` (light) — ограничивается названием и
        ``key_skills``.
        """
        parts: list[str] = []
        name = vacancy.get("name")
        if name:
            parts.append(f"Вакансия: {name}")

        if full_vacancy:
            description = full_vacancy.get("description")
            if description:
                parts.append(f"Описание: {strip_tags(description)}")
        else:
            if vacancy.get("id") and not include_full:
                key_skills = self.get_vacancy_key_skills(vacancy["id"])
                if key_skills:
                    parts.append(f"Ключевые навыки: {key_skills}")

        return "\n".join(parts)

    # ─── AI-проверка релевантности ────────────────────────────────

    def is_suitable_heavy(self, vacancy: dict[str, Any]) -> RelevanceResult:
        """Тяжёлая AI-проверка (с загрузкой полного описания вакансии)."""
        full_vacancy = None
        if vacancy.get("id"):
            try:
                full_vacancy = self.api_client.get(
                    f"/vacancies/{vacancy['id']}"
                )
            except (requests.RequestException, ApiError, BadResponse) as ex:
                logger.warning(
                    "Не удалось получить полную вакансию %s: %s",
                    vacancy.get("id"),
                    ex,
                )

        vacancy_info = self.build_vacancy_context(
            vacancy, full_vacancy=full_vacancy, include_full=True
        )
        prompt = f"Вакансия: {vacancy_info}"
        return self._ask_ai_suitability(
            prompt, vacancy.get("name", ""), "(heavy)"
        )

    def is_suitable_light(self, vacancy: dict[str, Any]) -> RelevanceResult:
        """Лёгкая AI-проверка (без описания)."""
        vacancy_info = self.build_vacancy_context(vacancy, include_full=False)
        prompt = f"Вакансия: {vacancy_info}"
        return self._ask_ai_suitability(
            prompt, vacancy.get("name", ""), "(light)"
        )

    def _ask_ai_suitability(
        self, prompt: str, vacancy_name: str, log_suffix: str = ""
    ) -> RelevanceResult:
        """Запрашивает у AI решение по вакансии с retry при невалидном JSON.

        Если ``ai_client`` не задан — возвращает ``RelevanceResult(suitable=True)``
        (т.е. вакансия считается подходящей, фильтрация выключена).

        При сбое AI поведение зависит от ``ai_failure_mode`` (issue #28):
        - ``"permissive"`` — ``suitable=True`` (не блокировать);
        - ``"strict"`` — ``suitable=False`` (отклонить при неуверенности);
        - ``"raise"`` — исключение пробрасывается наверх.

        После успешного парсинга применяет ``relevance_rules`` (issue #4):
        ``min_score`` / ``reject_if_primary`` могут «дожать» результат
        в ``suitable=False``.
        """
        if not self.ai_client:
            return RelevanceResult(suitable=True)

        for attempt in range(MAX_RETRIES):
            try:
                response = self.ai_client.complete(prompt).strip()
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "AI %s ответ (попытка %d): %s",
                        log_suffix,
                        attempt + 1,
                        response,
                    )

                result = parse_ai_json_response(response)
                if result is not None:
                    _apply_relevance_rules(result, self.relevance_rules)
                    if not result.suitable:
                        logger.info(
                            "Вакансия %s отклонена AI %s",
                            vacancy_name,
                            log_suffix,
                        )
                    return result

                logger.warning(
                    "AI %s не дал валидный JSON для вакансии %s (попытка %d/%d)",
                    log_suffix,
                    vacancy_name,
                    attempt + 1,
                    MAX_RETRIES,
                )
            except AIError as ex:
                logger.error("Ошибка AI %s: %s", log_suffix, ex)
                return self._handle_ai_failure(
                    str(ex), vacancy_name, log_suffix
                )

        logger.warning(
            "AI %s не дал валидный JSON после %d попыток для вакансии %s",
            log_suffix,
            MAX_RETRIES,
            vacancy_name,
        )
        return self._handle_ai_failure(
            "max_retries_exceeded", vacancy_name, log_suffix
        )

    def _handle_ai_failure(
        self, reason: str, vacancy_name: str, log_suffix: str
    ) -> RelevanceResult:
        """Обрабатывает сбой AI согласно ``ai_failure_mode`` (issue #28)."""
        if self._ai_failure_mode == "raise":
            raise AIError(
                f"AI failure ({log_suffix}) for {vacancy_name}: {reason}"
            )
        if self._ai_failure_mode == "strict":
            logger.info(
                "AI %s отклонил вакансию %s (strict mode): %s",
                log_suffix,
                vacancy_name,
                reason,
            )
            return RelevanceResult(
                suitable=False,
                raw_response=reason,
                reason=f"AI failure ({log_suffix}): {reason}",
            )
        # permissive (default, backward compatible)
        return RelevanceResult(suitable=True, raw_response=reason)
