"""AI-фильтрация вакансий по релевантности.

Извлечено из ``operations/apply_vacancies.py`` (issue #3). Сервис инкапсулирует
две стратегии:

- **heavy** — глубокий анализ (полное описание + опыт кандидата).
- **light** — быстрый матч по названию + skill_set.

Возвращает структурированный :class:`RelevanceResult`
(``suitable`` / ``score`` / ``reason``), который сохраняется в
``application_drafts.analysis_json``, ``relevance_score`` и
``relevance_reason`` (issue #4).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..ai.base import AIError
from ..utils.string import strip_tags

logger = logging.getLogger(__package__)

# Максимум попыток переспросить AI, если JSON невалидный
MAX_RETRIES = 3


@dataclass
class RelevanceResult:
    """Структурированный результат AI-фильтра.

    Attributes:
        suitable: итоговое решение — подходит ли кандидат.
        score: числовая оценка (0..100), если AI её вернул.
        reason: текстовое обоснование от AI.
        raw_response: исходный ответ AI (для отладки и
            ``application_drafts.analysis_json``).
    """

    suitable: bool
    score: int | None = None
    reason: str | None = None
    raw_response: str | None = None


def parse_ai_json_response(response: str) -> RelevanceResult | None:
    """Парсит ответ AI в :class:`RelevanceResult`.

    Поддерживает три формы ответа:
    - ``"да"/"yes"/"true"`` → ``RelevanceResult(suitable=True)``;
    - ``"нет"/"no"/"false"`` → ``RelevanceResult(suitable=False)``;
    - JSON ``{"suitable": bool, "score": int, "reason": str}`` или
      ``{"suitable": bool, "reason": str}``.

    Если ни одна форма не сработала — возвращает ``None`` (для retry в
    :meth:`RelevanceService._ask_ai_suitability`).
    """
    response = (response or "").strip()
    if not response:
        return None

    lower = response.lower()
    if lower in ("да", "yes", "true"):
        return RelevanceResult(suitable=True, raw_response=response)
    if lower in ("нет", "no", "false"):
        return RelevanceResult(suitable=False, raw_response=response)

    clean_json = re.sub(
        r"```json\s*|\s*```", "", response, flags=re.IGNORECASE
    ).strip()
    try:
        data = json.loads(clean_json)
        if isinstance(data, dict) and "suitable" in data:
            return _result_from_dict(data, response)
    except (ValueError, TypeError) as ex:
        logger.debug("JSON parse error: %s. Raw response: %s", ex, response)

    # Fallback: ищем первый подходящий JSON-блок
    json_match = re.search(
        r'\{[^{}]*"suitable"\s*:\s*(true|false)[^{}]*\}',
        response,
        re.IGNORECASE,
    )
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if isinstance(data, dict):
                return _result_from_dict(data, response)
        except (ValueError, TypeError):
            pass

    return None


def _result_from_dict(data: dict, raw: str) -> RelevanceResult:
    """Достаёт suitable/score/reason из dict ответа AI."""
    suitable = bool(data.get("suitable"))
    score = data.get("score")
    if score is not None:
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = None
    reason = data.get("reason")
    if reason is not None:
        reason = str(reason)
    return RelevanceResult(
        suitable=suitable, score=score, reason=reason, raw_response=raw
    )


def build_filter_system_prompt_heavy(resume_analysis: str) -> str:
    """System prompt для тяжёлого AI-фильтра."""
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

#### ВЫХОД (OUTPUT)
Ответ СТРОГО в формате JSON:
{{
  "suitable": true,
  "score": 85,
  "reason": "краткое профессиональное обоснование: какие именно навыки/достижения кандидата мэтчатся с задачами вакансии"
}}

---

### [CANDIDATE DATA]
{resume_analysis}
"""


def build_filter_system_prompt_light(resume_analysis: str) -> str:
    """System prompt для лёгкого AI-фильтра."""
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

Ответ только JSON:
{{"suitable": true, "score": 80, "reason": "..."}} или {{"suitable": false, "reason": "..."}}

Кандидат:
{resume_analysis}
"""


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
    """

    def __init__(self, api_client: Any, ai_client: Any = None):
        self.api_client = api_client
        self.ai_client = ai_client
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
        """Лёгкий анализ резюме (только title + skill_set). Результат кешируется."""
        resume_id = resume.get("id")
        cache_key = (resume_id, "light")
        if cache_key in self._resume_analysis_cache:
            return self._resume_analysis_cache[cache_key]

        try:
            full_resume = self.api_client.get(f"/resumes/{resume_id}")
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

    # ─── Контекст вакансии (для промпта) ───────────────────────────

    def get_vacancy_key_skills(self, vacancy_id: str | int) -> str:
        """Возвращает key_skills вакансии одной строкой."""
        try:
            full_vacancy = self.api_client.get(f"/vacancies/{vacancy_id}")
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
        При ``AIError`` — также ``suitable=True`` с raw_response, чтобы не
        блокировать работу из-за сбоя AI.
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
                return RelevanceResult(suitable=True, raw_response=str(ex))

        logger.warning(
            "AI %s не дал валидный JSON после %d попыток для вакансии %s",
            log_suffix,
            MAX_RETRIES,
            vacancy_name,
        )
        return RelevanceResult(suitable=True)
