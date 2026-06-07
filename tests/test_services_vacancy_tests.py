"""Тесты сервиса тестов вакансий (issue #3)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hh_applicant_tool.services.vacancy_tests import (
    REFUSAL_WITH_LINK_TEMPLATE,
    SUBMIT_DELAY_RANGE,
    VacancyTestsService,
    fetch_vacancy_tests,
)

# ─── fetch_vacancy_tests (module-level) ──────────────────────────────


def _make_response_with_tests(tests_json: str) -> MagicMock:
    """Создаёт mock session.get, который возвращает HTML с блоком vacancyTests."""
    r = MagicMock()
    r.text = (
        f'<html>someprefix</html>,"vacancyTests":{tests_json},'
        '"counters":{"foo":"bar"}'
    )
    return r


def test_fetch_vacancy_tests_parses_block():
    session = MagicMock()
    tests_payload = (
        '{"42":{"uidPk":"u","guid":"g","startTime":"t",'
        '"required":"true","tasks":[]}}'
    )
    session.get.return_value = _make_response_with_tests(tests_payload)

    data = fetch_vacancy_tests(session, "https://hh.ru/applicant/...")

    assert "42" in data
    assert data["42"]["uidPk"] == "u"


def test_fetch_vacancy_tests_missing_marker():
    session = MagicMock()
    r = MagicMock()
    r.text = "<html>no marker here</html>"
    session.get.return_value = r

    import pytest

    with pytest.raises(ValueError, match="tests not found"):
        fetch_vacancy_tests(session, "https://hh.ru/applicant/...")


def test_fetch_vacancy_tests_invalid_json():
    import pytest

    session = MagicMock()
    session.get.return_value = _make_response_with_tests("not valid json{")

    with pytest.raises(ValueError, match="Не могу распарсить vacancyTests"):
        fetch_vacancy_tests(session, "https://hh.ru/applicant/...")


def test_fetch_vacancy_tests_calls_get_with_url():
    session = MagicMock()
    session.get.return_value = _make_response_with_tests(
        '{"1":{"uidPk":"u","guid":"g","startTime":"t","required":"true","tasks":[]}}'
    )

    fetch_vacancy_tests(session, "https://hh.ru/applicant/test_url")

    session.get.assert_called_once_with("https://hh.ru/applicant/test_url")


# ─── VacancyTestsService.fetch_tests ────────────────────────────────


def test_service_fetch_tests_delegates_to_module():
    session = MagicMock()
    session.get.return_value = _make_response_with_tests(
        '{"5":{"uidPk":"u","guid":"g","startTime":"t","required":"true","tasks":[]}}'
    )
    svc = VacancyTestsService(session=session, ai_client=None)

    data = svc.fetch_tests("https://hh.ru/applicant/...")

    assert "5" in data


# ─── prepare_answers (без HTTP) ─────────────────────────────────────


def _make_test_data(
    tasks: list[dict],
    *,
    uidPk: str = "u1",
    guid: str = "g1",
    start_time: str = "t1",
    required: str = "true",
) -> dict:
    return {
        "uidPk": uidPk,
        "guid": guid,
        "name": "Test",
        "description": "Test description",
        "required": required,
        "startTime": start_time,
        "tasks": tasks,
    }


def test_prepare_answers_choice_with_ai():
    """Choice + AI: AI возвращает id решения."""
    ai = MagicMock()
    ai.complete.return_value = "2"
    svc = VacancyTestsService(session=MagicMock(), ai_client=ai)

    test_data = _make_test_data(
        [
            {
                "id": 1,
                "description": "Вопрос",
                "multiple": "false",
                "open": "false",
                "candidateSolutions": [
                    {"id": "1", "text": "Вариант 1"},
                    {"id": "2", "text": "Вариант 2"},
                    {"id": "3", "text": "Вариант 3"},
                ],
            }
        ]
    )

    answers = svc.prepare_answers(test_data)

    assert len(answers) == 1
    a = answers[0]
    assert a.task_id == "1"
    assert a.answer_type == "choice"
    assert a.selected_solution_id == "2"
    assert a.generated_answer == "2"
    assert a.options_json is not None
    assert len(a.options_json) == 3
    assert a.review_status == "generated"
    assert a.draft_id == 0


def test_prepare_answers_choice_ai_with_surrounding_text():
    """AI может вернуть 'Я думаю, ответ 3' — вытаскиваем первый ID."""
    ai = MagicMock()
    ai.complete.return_value = "Ответ: 3"
    svc = VacancyTestsService(session=MagicMock(), ai_client=ai)

    test_data = _make_test_data(
        [
            {
                "id": 1,
                "description": "Вопрос",
                "multiple": "false",
                "open": "false",
                "candidateSolutions": [
                    {"id": "1", "text": "A"},
                    {"id": "3", "text": "B"},
                ],
            }
        ]
    )

    answers = svc.prepare_answers(test_data)

    assert answers[0].selected_solution_id == "3"


def test_prepare_answers_choice_ai_falls_back_to_first():
    """Если AI вернул мусор без цифр — берётся первый вариант."""
    ai = MagicMock()
    ai.complete.return_value = "не знаю"
    svc = VacancyTestsService(session=MagicMock(), ai_client=ai)

    test_data = _make_test_data(
        [
            {
                "id": 1,
                "description": "?",
                "multiple": "false",
                "open": "false",
                "candidateSolutions": [
                    {"id": "7", "text": "A"},
                    {"id": "8", "text": "B"},
                ],
            }
        ]
    )

    answers = svc.prepare_answers(test_data)

    assert answers[0].selected_solution_id == "7"


def test_prepare_answers_choice_rule_yes():
    """Без AI — выбираем вариант 'да' (точное совпадение, lowercase)."""
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)

    test_data = _make_test_data(
        [
            {
                "id": 1,
                "description": "Вопрос",
                "multiple": "false",
                "open": "false",
                "candidateSolutions": [
                    {"id": "1", "text": "да"},
                    {"id": "2", "text": "нет"},
                ],
            }
        ]
    )

    answers = svc.prepare_answers(test_data)

    assert answers[0].answer_type == "choice"
    assert answers[0].selected_solution_id == "1"


def test_prepare_answers_choice_rule_middle():
    """Без 'да' — берём середину."""
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)

    test_data = _make_test_data(
        [
            {
                "id": 1,
                "description": "Вопрос",
                "multiple": "false",
                "open": "false",
                "candidateSolutions": [
                    {"id": "1", "text": "A"},
                    {"id": "2", "text": "B"},
                    {"id": "3", "text": "C"},
                ],
            }
        ]
    )

    answers = svc.prepare_answers(test_data)

    assert answers[0].selected_solution_id == "2"


def test_prepare_answers_text_with_link_uses_refusal():
    """Вопрос со ссылкой → REFUSAL_WITH_LINK_TEMPLATE (через rand_text)."""
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)

    test_data = _make_test_data(
        [
            {
                "id": 1,
                "description": "Заполните анкету: https://forms.gle/abc",
                "multiple": "false",
                "open": "true",
                "candidateSolutions": [],
            }
        ]
    )

    with patch(
        "hh_applicant_tool.services.vacancy_tests.rand_text",
        return_value="Простите, не перехожу по внешним ссылкам",
    ) as mock_rand:
        answers = svc.prepare_answers(test_data)

    assert answers[0].answer_type == "text"
    assert "Простите" in answers[0].generated_answer
    mock_rand.assert_called_once_with(REFUSAL_WITH_LINK_TEMPLATE)


def test_prepare_answers_text_with_ai():
    """Текстовый вопрос + AI — AI зовётся, ответ — его выход."""
    ai = MagicMock()
    ai.complete.return_value = "Краткий профессиональный ответ"
    svc = VacancyTestsService(session=MagicMock(), ai_client=ai)

    test_data = _make_test_data(
        [
            {
                "id": 1,
                "description": "Расскажите о себе",
                "multiple": "false",
                "open": "true",
                "candidateSolutions": [],
            }
        ]
    )

    answers = svc.prepare_answers(test_data)

    assert answers[0].answer_type == "text"
    assert answers[0].generated_answer == "Краткий профессиональный ответ"
    ai.complete.assert_called_once()


def test_prepare_answers_text_default_yes():
    """Без ссылки и без AI — дефолтный ответ 'Да'."""
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)

    test_data = _make_test_data(
        [
            {
                "id": 1,
                "description": "Готовы ли вы?",
                "multiple": "false",
                "open": "true",
                "candidateSolutions": [],
            }
        ]
    )

    answers = svc.prepare_answers(test_data)

    assert answers[0].answer_type == "text"
    assert answers[0].generated_answer == "Да"


def test_prepare_answers_mixed_tasks():
    """Несколько задач разных типов."""
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)

    test_data = _make_test_data(
        [
            {
                "id": 1,
                "description": "Choice 1",
                "multiple": "false",
                "open": "false",
                "candidateSolutions": [
                    {"id": "1", "text": "да"},
                    {"id": "2", "text": "нет"},
                ],
            },
            {
                "id": 2,
                "description": "Текст",
                "multiple": "false",
                "open": "true",
                "candidateSolutions": [],
            },
        ]
    )

    answers = svc.prepare_answers(test_data)

    assert len(answers) == 2
    assert answers[0].answer_type == "choice"
    assert answers[0].selected_solution_id == "1"
    assert answers[1].answer_type == "text"
    assert answers[1].generated_answer == "Да"


# ─── build_apply_payload_from_answers ───────────────────────────────


def _make_full_test_data() -> dict:
    return _make_test_data(
        [
            {
                "id": 5,
                "description": "Вопрос",
                "multiple": "false",
                "open": "false",
                "candidateSolutions": [
                    {"id": "1", "text": "да"},
                    {"id": "2", "text": "нет"},
                ],
            }
        ]
    )


def test_build_apply_payload_includes_xsrf_and_meta():
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)
    test_data = _make_full_test_data()

    payload = svc.build_apply_payload_from_answers(
        test_data,
        answers=[],
        vacancy_id=42,
        resume_hash="rhash",
        letter="",
        xsrf_token="XSRF-TOKEN",
    )

    assert payload["_xsrf"] == "XSRF-TOKEN"
    assert payload["uidPk"] == "u1"
    assert payload["guid"] == "g1"
    assert payload["startTime"] == "t1"
    assert payload["testRequired"] == "true"
    assert payload["vacancy_id"] == 42
    assert payload["resume_hash"] == "rhash"
    assert payload["ignore_postponed"] == "true"
    assert payload["incomplete"] == "false"
    assert payload["mark_applicant_visible_in_vacancy_country"] == "false"
    assert payload["country_ids"] == "[]"
    assert payload["lux"] == "true"
    assert payload["withoutTest"] == "no"
    assert payload["letter"] == ""


def test_build_apply_payload_uses_answer_for_known_task():
    """Если передан ответ с selected_solution_id — он используется."""
    from hh_applicant_tool.storage.models.application_test_answer import (
        ApplicationTestAnswerModel,
    )

    svc = VacancyTestsService(session=MagicMock(), ai_client=None)
    test_data = _make_full_test_data()
    answer = ApplicationTestAnswerModel(
        draft_id=0,
        task_id="5",
        question="?",
        answer_type="choice",
        selected_solution_id="7",
    )

    payload = svc.build_apply_payload_from_answers(
        test_data,
        answers=[answer],
        vacancy_id=42,
        resume_hash="r",
        letter="",
        xsrf_token="X",
    )

    assert payload["task_5"] == "7"


def test_build_apply_payload_fallback_for_missing_answer():
    """Без ответа — fallback на середину (или 'да')."""
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)
    test_data = _make_full_test_data()

    payload = svc.build_apply_payload_from_answers(
        test_data,
        answers=[],  # пусто
        vacancy_id=42,
        resume_hash="r",
        letter="",
        xsrf_token="X",
    )

    # Без AI fallback: первый вариант — "да"
    assert payload["task_5"] == "1"


def test_build_apply_payload_fallback_middle_when_no_yes():
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)
    test_data = _make_test_data(
        [
            {
                "id": 5,
                "description": "?",
                "multiple": "false",
                "open": "false",
                "candidateSolutions": [
                    {"id": "1", "text": "A"},
                    {"id": "2", "text": "B"},
                    {"id": "3", "text": "C"},
                ],
            }
        ]
    )

    payload = svc.build_apply_payload_from_answers(
        test_data,
        answers=[],
        vacancy_id=42,
        resume_hash="r",
        letter="",
        xsrf_token="X",
    )

    # Из 3 вариантов — середина
    assert payload["task_5"] == "2"


def test_build_apply_payload_text_uses_generated():
    """Текстовый ответ — поле task_X_text."""
    from hh_applicant_tool.storage.models.application_test_answer import (
        ApplicationTestAnswerModel,
    )

    svc = VacancyTestsService(session=MagicMock(), ai_client=None)
    test_data = _make_test_data(
        [
            {
                "id": 5,
                "description": "?",
                "multiple": "false",
                "open": "true",
                "candidateSolutions": [],
            }
        ]
    )
    answer = ApplicationTestAnswerModel(
        draft_id=0,
        task_id="5",
        question="?",
        answer_type="text",
        generated_answer="Мой ответ",
    )

    payload = svc.build_apply_payload_from_answers(
        test_data,
        answers=[answer],
        vacancy_id=42,
        resume_hash="r",
        letter="",
        xsrf_token="X",
    )

    assert payload["task_5_text"] == "Мой ответ"


def test_build_apply_payload_text_fallback_yes():
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)
    test_data = _make_test_data(
        [
            {
                "id": 5,
                "description": "?",
                "multiple": "false",
                "open": "true",
                "candidateSolutions": [],
            }
        ]
    )

    payload = svc.build_apply_payload_from_answers(
        test_data,
        answers=[],
        vacancy_id=42,
        resume_hash="r",
        letter="",
        xsrf_token="X",
    )

    assert payload["task_5_text"] == "Да"


def test_build_apply_payload_includes_letter():
    svc = VacancyTestsService(session=MagicMock(), ai_client=None)
    test_data = _make_full_test_data()

    payload = svc.build_apply_payload_from_answers(
        test_data,
        answers=[],
        vacancy_id=42,
        resume_hash="r",
        letter="My cover letter",
        xsrf_token="X",
    )

    assert payload["letter"] == "My cover letter"


# ─── submit_apply ───────────────────────────────────────────────────


def test_submit_apply_posts_payload():
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {"ok": True}
    response.request.method = "POST"
    response.url = "https://hh.ru/applicant/vacancy_response/popup"
    response.status_code = 200
    session.post.return_value = response

    svc = VacancyTestsService(session=session, ai_client=None)
    payload = {"_xsrf": "X", "task_5": "1"}

    with patch(
        "hh_applicant_tool.services.vacancy_tests.time.sleep"
    ) as mock_sleep:
        result = svc.submit_apply(
            "https://hh.ru/applicant/vacancy_response?vacancyId=42",
            payload,
            xsrf_token="XSRF-TOKEN",
        )

    assert result == {"ok": True}
    mock_sleep.assert_called_once()
    # Проверяем диапазон
    delay_arg = mock_sleep.call_args[0][0]
    assert SUBMIT_DELAY_RANGE[0] <= delay_arg <= SUBMIT_DELAY_RANGE[1]
    # POST отправлен
    session.post.assert_called_once()
    url = session.post.call_args[0][0]
    assert url == "https://hh.ru/applicant/vacancy_response/popup"
    # data = payload
    assert session.post.call_args[1]["data"] == payload
    # headers
    headers = session.post.call_args[1]["headers"]
    assert headers["Referer"] == (
        "https://hh.ru/applicant/vacancy_response?vacancyId=42"
    )
    assert headers["X-Xsrftoken"] == "XSRF-TOKEN"
    assert headers["X-Hhtmfrom"] == "vacancy"
    assert headers["X-Hhtmsource"] == "vacancy_response"
    assert headers["X-Requested-With"] == "XMLHttpRequest"


# ─── SUBMIT_DELAY_RANGE ─────────────────────────────────────────────


def test_submit_delay_range_is_2_to_3():
    """Защита от случайного изменения диапазона задержки."""
    assert SUBMIT_DELAY_RANGE == (2.0, 3.0)
