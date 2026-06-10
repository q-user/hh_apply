"""Relevance handler - AI-based vacancy filtering with heavy/light strategies."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from job_bot.application_prep.models.relevance import (
    MAX_RETRIES,
    SCORE_MAX,
    SCORE_MIN,
    RelevanceResult,
)
from job_bot.application_prep.repositories.relevance_repo import (
    RelevanceAnalysisRepository,
)
from job_bot.shared.api.client import HHApiClient
from job_bot.shared.storage.database import Database

if TYPE_CHECKING:
    from job_bot.shared.ai.client import AIClient

logger = logging.getLogger(__package__)


# AIError base exception - kept for parity with original module
class AIError(Exception):
    """Base AI error."""


class RelevanceHandler:
    """Handler for relevance analysis (heavy/light).

    Implements RelevancePort and RelevanceStoragePort.
    """

    def __init__(
        self,
        database: Database,
        api_client: HHApiClient | None = None,
        ai_client: "AIClient | None" = None,
        *,
        relevance_rules: dict[str, Any] | None = None,
        ai_failure_mode: str = "permissive",
    ) -> None:
        if ai_failure_mode not in ("permissive", "strict", "raise"):
            raise ValueError(
                f"ai_failure_mode must be 'permissive', 'strict', or 'raise', "
                f"got {ai_failure_mode!r}"
            )
        self._api_client = api_client
        self._ai_client = ai_client
        self._relevance_rules = relevance_rules
        self._ai_failure_mode = ai_failure_mode
        self._repo = RelevanceAnalysisRepository(database)
        # Cache for heavy resume analysis
        self._resume_analysis_cache: dict[tuple[str | None, str], str] = {}

    # ─── Resume analysis (with cache) ─────────────────────────

    def analyze_resume_heavy(self, resume: dict[str, Any]) -> str:
        """Heavy resume analysis (full text + experience). Result is cached."""
        resume_id = resume.get("id")
        cache_key = (resume_id, "heavy")
        if cache_key in self._resume_analysis_cache:
            return self._resume_analysis_cache[cache_key]

        if resume_id and self._api_client is not None:
            try:
                full_resume = self._api_client.get(f"/resumes/{resume_id}")
            except Exception as ex:
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
        """Light resume analysis (title + skill_set only). Result is cached."""
        resume_id = resume.get("id")
        cache_key = (resume_id, "light")
        if cache_key in self._resume_analysis_cache:
            return self._resume_analysis_cache[cache_key]

        if resume_id and self._api_client is None:
            return ""

        try:
            full_resume = (
                self._api_client.get(f"/resumes/{resume_id}")
                if self._api_client
                else {}
            )
        except Exception as ex:
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

    # ─── Vacancy context (for prompt) ───────────────────────────

    def get_vacancy_key_skills(self, vacancy_id: str | int) -> str:
        """Returns key_skills of vacancy as a single string."""
        if self._api_client is None:
            return ""
        try:
            full_vacancy = self._api_client.get(f"/vacancies/{vacancy_id}")
            key_skills_data = full_vacancy.get("key_skills") or []
            return ", ".join(
                s["name"] for s in key_skills_data if s.get("name")
            )
        except Exception as ex:
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
        """Build text description of vacancy for prompt.

        include_full=True (heavy) - inserts description from full_vacancy.
        include_full=False (light) - limited to name and key_skills.
        """
        parts: list[str] = []
        name = vacancy.get("name")
        if name:
            parts.append(f"Вакансия: {name}")

        if full_vacancy:
            description = full_vacancy.get("description")
            if description:
                parts.append(f"Описание: {self._strip_tags(description)}")
        else:
            if vacancy.get("id") and not include_full:
                key_skills = self.get_vacancy_key_skills(vacancy["id"])
                if key_skills:
                    parts.append(f"Ключевые навыки: {key_skills}")

        return "\n".join(parts)

    # ─── AI relevance check ────────────────────────────────────

    def is_suitable_heavy(self, vacancy: dict[str, Any]) -> RelevanceResult:
        """Heavy AI check (with full vacancy description loaded)."""
        full_vacancy = None
        if vacancy.get("id") and self._api_client is not None:
            try:
                full_vacancy = self._api_client.get(
                    f"/vacancies/{vacancy['id']}"
                )
            except Exception as ex:
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
        """Light AI check (without description)."""
        vacancy_info = self.build_vacancy_context(vacancy, include_full=False)
        prompt = f"Вакансия: {vacancy_info}"
        return self._ask_ai_suitability(
            prompt, vacancy.get("name", ""), "(light)"
        )

    def _ask_ai_suitability(
        self, prompt: str, vacancy_name: str, log_suffix: str = ""
    ) -> RelevanceResult:
        """Request AI decision on vacancy with retry on invalid JSON.

        If ai_client is not set - returns RelevanceResult(suitable=True)
        (i.e. vacancy is considered suitable, filtering is disabled).

        On AI failure, behavior depends on ai_failure_mode (issue #28):
        - "permissive" - suitable=True (don't block);
        - "strict" - suitable=False (reject if uncertain);
        - "raise" - exception is propagated up.

        After successful parsing, applies relevance_rules (issue #4):
        min_score / reject_if_primary can "push" the result to suitable=False.
        """
        if not self._ai_client:
            return RelevanceResult(suitable=True)

        for attempt in range(MAX_RETRIES):
            try:
                response = self._ai_client.complete(prompt).strip()
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "AI %s ответ (попытка %d): %s",
                        log_suffix,
                        attempt + 1,
                        response,
                    )

                result = parse_ai_json_response(response)
                if result is not None:
                    _apply_relevance_rules(result, self._relevance_rules)
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
        """Handle AI failure according to ai_failure_mode (issue #28)."""
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

    @staticmethod
    def _strip_tags(html: str) -> str:
        """Strip HTML tags from a string."""
        from hh_applicant_tool.utils.string import strip_tags

        return strip_tags(html)

    # Implementation of RelevanceStoragePort

    def save_analysis(self, draft_id: str, result: RelevanceResult) -> None:
        """Save RelevanceResult linked to a draft."""
        self._repo.save_analysis(draft_id, result)

    def get_analysis(self, draft_id: str) -> RelevanceResult | None:
        """Get RelevanceResult by draft ID."""
        return self._repo.get_analysis(draft_id)


# ─── helpers for normalizing AI response ─────────────────────────


def _as_int_0_100(value: Any) -> int | None:
    """Cast value to int in 0..100 range.

    Returns None for non-numeric values, empty strings, None,
    and for values that cannot be cast to int.
    Rounds floats, clamps range.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # bool is a subclass of int, but semantically it's not a number;
        # True/False for score is meaningless.
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
    """Cast value to list[str] (None if empty/not list)."""
    if value is None:
        return None
    if isinstance(value, str):
        # AI sometimes returns a string instead of an array - take it as a single element
        # if it's not empty.
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


# ─── AI response parsing ──────────────────────────────────────────


# Regex for fallback JSON block search with "suitable" inside text.
_FALLBACK_JSON_RE = re.compile(
    r"\{[^{}]*\"suitable\"\s*:\s*(?:true|false)[^{}]*\}",
    re.IGNORECASE,
)


def _strip_markdown_fence(text: str) -> str:
    """Strip ```json ... ``` enclosing blocks without damaging JSON.

    Removes ONLY the enclosing fence (```json and ```), if they are at the
    beginning/end of the text. Internal ``` are not touched.
    """
    s = text.strip()
    if s.startswith("```"):
        # Cut the first line (```json / ```)
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        else:
            s = s.lstrip("`")
    if s.endswith("```"):
        s = s[:-3].rstrip()
    return s


def parse_ai_json_response(response: str) -> RelevanceResult | None:
    """Parse AI response into RelevanceResult.

    Supported response forms (issue #4):
    - "да"/"yes"/"true" -> RelevanceResult(suitable=True);
    - "нет"/"no"/"false" -> RelevanceResult(suitable=False);
    - JSON {"suitable": bool, ...} - arbitrary set of fields from extended contract
      (relevance_score, primary_stack, risks, etc.). Legacy score field is
      mapped to relevance_score;
    - Same JSON wrapped in markdown fence ```json ... ```;
    - Same JSON embedded in arbitrary text (fallback regex).

    If no form matched - returns None (for retry in _ask_ai_suitability).
    """
    if response is None:
        return None
    text = str(response).strip()
    if not text:
        return None

    # Boolean-only response (case-insensitive). Score is not filled per spec.
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

    # Fallback: search for JSON block with "suitable" in arbitrary text.
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
    """Build RelevanceResult from AI response dict.

    Legacy field score is treated as alias relevance_score (issue #4).
    When both fields are present, relevance_score is preferred.
    """
    suitable = bool(data.get("suitable"))

    # legacy score -> relevance_score
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
    """Return str(value) or None for empty values."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# ─── format relevance_rules for system prompt ─────────────────────


def _format_relevance_rules(rules: dict[str, Any] | None) -> str:
    """Render SearchProfileModel.relevance_rules to text for system prompt.

    Returns empty string if rules are absent or empty.
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
    """System prompt for heavy AI filter (issue #4).

    Requires AI STRICT JSON of the following form::

        {
          "suitable": true,
          "relevance_score": 92,
          ...
        }
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
    """System prompt for light AI filter (issue #4)."""
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


# ─── applying relevance_rules to AI result ────────────────────────


def _apply_relevance_rules(
    result: RelevanceResult,
    rules: dict[str, Any] | None,
) -> RelevanceResult:
    """Apply relevance_rules to AI result (issue #4).

    Modifies result in place and returns the same object.

    Applied rules:
    - min_score - if relevance_score < min_score, then suitable=False
      (with reason in reason field).
    - reject_if_primary - if primary_stack contains element from this list,
      then suitable=False.

    allowed_secondary / nice_to_have / must_have / strict_role are NOT
    included here - they are communicated to AI in the system prompt.
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
        # Case-insensitive string match.
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
    """Append addition to existing with "; " separator (issue #4)."""
    if not existing:
        return addition
    if addition in existing:
        return existing
    return f"{existing}; {addition}"
