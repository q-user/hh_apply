"""Тесты сервиса генерации сопроводительных писем (issue #3)."""

from __future__ import annotations

import random
from unittest.mock import MagicMock

from hh_applicant_tool.services.cover_letters import (
    DEFAULT_LETTER_TEMPLATE,
    CoverLetterService,
)

# ─── Шаблон (без AI) ────────────────────────────────────────────────


def test_template_default():
    random.seed(0)  # детерминированный выбор вариантов в {a|b}
    svc = CoverLetterService(api_client=MagicMock())
    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={
            "first_name": "Иван",
            "vacancy_name": "Backend",
            "resume_title": "Senior",
        },
        force=True,
    )
    assert isinstance(out, str)
    assert "Иван" in out
    assert "Backend" in out
    # resume_title может как попасть, так и не попасть в выход (шаблон рандомно
    # выбирает между «мою кандидатуру» и «мое резюме «Senior»»). Проверяем,
    # что шаблон отработал — содержит имя.
    assert "Иван" in out


def test_template_required_by_vacancy():
    """force=False, но required_by_vacancy=True — письмо генерируется."""
    svc = CoverLetterService(api_client=MagicMock())
    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={
            "first_name": "Иван",
            "vacancy_name": "X",
            "resume_title": "T",
        },
        force=False,
        required_by_vacancy=True,
    )
    assert out
    assert "Иван" in out


def test_no_letter_when_not_required():
    """force=False И required_by_vacancy=False → пустая строка."""
    svc = CoverLetterService(api_client=MagicMock())
    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={"first_name": "Иван"},
    )
    assert out == ""


def test_no_letter_when_not_required_explicitly():
    svc = CoverLetterService(api_client=MagicMock())
    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={"first_name": "Иван"},
        force=False,
        required_by_vacancy=False,
    )
    assert out == ""


def test_custom_template():
    """Кастомный шаблон через template=... используется."""
    svc = CoverLetterService(
        api_client=MagicMock(),
        template="%(first_name)s - %(vacancy_name)s",
    )
    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={"first_name": "Иван", "vacancy_name": "Backend"},
        force=True,
    )
    assert "Иван" in out
    assert "Backend" in out


def test_default_template_is_set_on_construction():
    """Без template — используется DEFAULT_LETTER_TEMPLATE."""
    svc = CoverLetterService(api_client=MagicMock())
    assert svc.template == DEFAULT_LETTER_TEMPLATE


# ─── AI-путь ────────────────────────────────────────────────────────


def test_ai_path_returns_parsed_cover_letter():
    api = MagicMock()
    api.get.return_value = {
        "description": "<p>Job description</p>",
        "key_skills": [{"name": "Python"}, {"name": "Django"}],
    }
    ai = MagicMock()
    ai.complete.return_value = '{"cover_letter": "Уважаемые HR! Откликаюсь."}'
    svc = CoverLetterService(api, ai_client=ai)

    out = svc.generate(
        vacancy={"id": 7, "name": "Backend", "employer": {"name": "Acme"}},
        placeholders={"first_name": "Иван", "last_name": "Иванов"},
        resume={"title": "Senior Python"},
        resume_analysis="Большой опыт",
        force=True,
    )

    assert out == "Уважаемые HR! Откликаюсь."
    ai.complete.assert_called_once()
    # Контекст содержит описание вакансии и key_skills
    prompt = ai.complete.call_args[0][0]
    assert "Job description" in prompt
    assert "Python" in prompt
    assert "Acme" in prompt


def test_ai_fallback_to_raw_response():
    """Если AI вернул не-JSON — отдаём сырой текст как fallback."""
    api = MagicMock()
    api.get.return_value = {"description": ""}
    ai = MagicMock()
    ai.complete.return_value = "raw text without json"
    svc = CoverLetterService(api, ai_client=ai)

    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={"first_name": "И"},
        force=True,
    )

    assert out == "raw text without json"


def test_ai_fallback_to_raw_when_cover_letter_field_empty():
    """Если в JSON нет поля cover_letter или оно пустое — fallback."""
    api = MagicMock()
    api.get.return_value = {"description": ""}
    ai = MagicMock()
    ai.complete.return_value = '{"other_field": "value"}'
    svc = CoverLetterService(api, ai_client=ai)

    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={"first_name": "И"},
        force=True,
    )

    # Парсер не нашёл cover_letter, fallback на raw_response
    assert out == '{"other_field": "value"}'


def test_ai_fallback_to_raw_when_json_invalid():
    api = MagicMock()
    api.get.return_value = {"description": ""}
    ai = MagicMock()
    ai.complete.return_value = '```json\n{"cover_letter": "OK!"}\n```'
    svc = CoverLetterService(api, ai_client=ai)

    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={"first_name": "И"},
        force=True,
    )

    # Парсер умеет снимать fence-обёртку
    assert out == "OK!"


def test_ai_vacancy_fetch_failure_uses_empty_description():
    """Если /vacancies/{id} падает — генерация всё равно работает."""
    api = MagicMock()
    api.get.side_effect = RuntimeError("network down")
    ai = MagicMock()
    ai.complete.return_value = '{"cover_letter": "Сгенерировано без описания"}'
    svc = CoverLetterService(api, ai_client=ai)

    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={"first_name": "И"},
        force=True,
    )

    assert out == "Сгенерировано без описания"
    ai.complete.assert_called_once()


def test_ai_vacancy_fetch_failure_raw_response():
    """Если /vacancies/{id} падает + AI не-JSON — отдаём raw."""
    api = MagicMock()
    api.get.side_effect = RuntimeError("network down")
    ai = MagicMock()
    ai.complete.return_value = "fallback text"
    svc = CoverLetterService(api, ai_client=ai)

    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={"first_name": "И"},
        force=True,
    )

    assert out == "fallback text"


def test_ai_required_by_vacancy():
    """required_by_vacancy=True с AI — вызывает AI."""
    api = MagicMock()
    api.get.return_value = {"description": ""}
    ai = MagicMock()
    ai.complete.return_value = '{"cover_letter": "AI letter"}'
    svc = CoverLetterService(api, ai_client=ai)

    out = svc.generate(
        vacancy={"id": 1, "name": "X", "response_letter_required": True},
        placeholders={"first_name": "И"},
        force=False,
        required_by_vacancy=True,
    )

    assert out == "AI letter"


def test_ai_doesnt_call_when_not_required():
    """force=False, required_by_vacancy=False → AI не зовётся."""
    api = MagicMock()
    ai = MagicMock()
    svc = CoverLetterService(api, ai_client=ai)

    out = svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={"first_name": "И"},
    )

    assert out == ""
    ai.complete.assert_not_called()


def test_ai_prompt_includes_candidate_info():
    """Промпт содержит first_name, last_name, resume_title, analysis."""
    api = MagicMock()
    api.get.return_value = {"description": ""}
    ai = MagicMock()
    ai.complete.return_value = '{"cover_letter": "x"}'
    svc = CoverLetterService(api, ai_client=ai)

    svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={
            "first_name": "Иван",
            "last_name": "Иванов",
        },
        resume={"title": "Senior Python Developer"},
        resume_analysis="10 лет в Python",
        force=True,
    )

    prompt = ai.complete.call_args[0][0]
    assert "Иван" in prompt
    assert "Иванов" in prompt
    assert "Senior Python Developer" in prompt
    assert "10 лет в Python" in prompt


def test_ai_default_first_name_when_missing():
    """Без first_name в placeholders — используется 'Кандидат'."""
    api = MagicMock()
    api.get.return_value = {"description": ""}
    ai = MagicMock()
    ai.complete.return_value = '{"cover_letter": "x"}'
    svc = CoverLetterService(api, ai_client=ai)

    svc.generate(
        vacancy={"id": 1, "name": "X"},
        placeholders={},  # без first_name
        force=True,
    )

    prompt = ai.complete.call_args[0][0]
    assert "Кандидат" in prompt
