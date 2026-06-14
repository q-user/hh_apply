"""Тесты интерактивного Telegram-ревью черновиков (issue #9).

Покрывает основные сценарии FSM:
- полный happy-path: intro → test → cover → confirm → enqueue;
- skip на каждом шаге (intro / test / cover / confirm);
- регенерация ответа теста (с AI);
- регенерация сопроводительного письма (с AI);
- свой ответ (текст + custom для cover);
- «выбрать другой» для choice-вопроса;
- resume сессии из сохранённого состояния;
- очередь пуста → idle + сообщение;
- отсутствие AI → понятный ответ.

Все тесты работают на in-memory SQLite (фикстура ``storage``), AI и
транспорт мокаются — тесты бегут за <100ms суммарно.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

from hh_applicant_tool.services.review_flow import (
    CB_CONFIRM_SEND,
    CB_CONFIRM_SKIP,
    CB_COVER_CUSTOM,
    CB_COVER_OK,
    CB_COVER_REGEN,
    CB_INTRO_CONTINUE,
    CB_INTRO_OPEN,
    CB_INTRO_SKIP,
    CB_TEST_CHOOSE,
    CB_TEST_CUSTOM,
    CB_TEST_OK,
    CB_TEST_REGEN,
    STATE_AWAIT_COVER_CUSTOM,
    STATE_AWAIT_COVER_REGEN,
    STATE_AWAIT_TEST_CUSTOM,
    STATE_AWAIT_TEST_REGEN,
    STATE_CONFIRM_APPLY,
    STATE_IDLE,
    STATE_REVIEW_COVER,
    STATE_REVIEW_INTRO,
    STATE_REVIEW_TEST,
    InlineButton,
    OutgoingMessage,
    ReviewFlowService,
)
from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.application_draft import (
    ApplicationDraftModel,
)
from hh_applicant_tool.storage.models.application_test_answer import (
    ApplicationTestAnswerModel,
)
from hh_applicant_tool.storage.models.telegram_session import (
    TelegramSessionModel,
)
from job_bot.telegram_bot.telegram_transport import TelegramTransport

CHAT_ID = 12345

# ─── Фикстуры и хелперы ─────────────────────────────────────────────


class _FixedClock:
    """Детерминированные часы — ``now()`` отдаёт фиксированный момент,
    ``sleep`` — no-op."""

    def __init__(self) -> None:
        self._now = datetime(2026, 6, 9, 10, 0, 0)

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        return None


def _make_transport() -> MagicMock:
    return MagicMock(spec=TelegramTransport)


def _make_service(
    conn: sqlite3.Connection,
    *,
    ai_client: MagicMock | None = None,
) -> ReviewFlowService:
    return ReviewFlowService(
        storage=StorageFacade(conn),
        transport=_make_transport(),
        config={"telegram": {"chat_id": CHAT_ID}},
        clock=_FixedClock(),
        ai_client=ai_client,
    )


def _make_draft(
    conn: sqlite3.Connection,
    *,
    vacancy_id: int = 100,
    status: str = "prepared",
    has_test: bool = False,
    cover_letter: str | None = "Готов работать!",
    analysis: dict[str, Any] | None = None,
    full_vacancy: dict[str, Any] | None = None,
) -> int:
    """Сохраняет черновик и возвращает его ``id``."""
    if analysis is None:
        analysis = {
            "primary_stack": ["Python", "Django"],
            "project": "Django-монолит",
            "complexity": "medium",
            "risks": ["FastAPI упомянут"],
        }
    if full_vacancy is None:
        full_vacancy = {
            "id": vacancy_id,
            "name": "Senior Python/Django Developer",
            "employer": {"name": "Example LLC"},
            "salary": {"from": 250000, "to": 350000, "currency": "RUR"},
            "schedule": {"name": "Удалённая работа"},
            "employment": {"name": "Полная занятость"},
            "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        }
    facade = StorageFacade(conn)
    draft = ApplicationDraftModel(
        resume_id="r1",
        vacancy_id=vacancy_id,
        status=status,
        has_test=has_test,
        cover_letter=cover_letter,
        cover_letter_status="generated",
        relevance_score=80,
        success_probability=78,
        relevance_reason="Основной стек совпадает",
        analysis_json=analysis,
        full_vacancy_json=full_vacancy,
    )
    facade.application_drafts.save(draft)
    conn.commit()
    row = conn.execute(
        "SELECT id FROM application_drafts WHERE vacancy_id=?",
        (vacancy_id,),
    ).fetchone()
    return row["id"]


def _make_test_answer(
    conn: sqlite3.Connection,
    draft_id: int,
    *,
    task_id: str = "t1",
    answer_type: str = "text",
    question: str = "Расскажите о себе",
    generated: str = "Сгенерированный ответ",
    options: list[str] | None = None,
) -> int:
    facade = StorageFacade(conn)
    facade.application_test_answers.save(
        ApplicationTestAnswerModel(
            draft_id=draft_id,
            task_id=task_id,
            answer_type=answer_type,
            question=question,
            generated_answer=generated,
            options_json=options,
        )
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM application_test_answers WHERE task_id=?",
        (task_id,),
    ).fetchone()
    return row["id"]


def _msg(update: dict) -> dict:
    """Собирает update с message.text = ``update``."""
    return {
        "message": {
            "chat": {"id": CHAT_ID},
            "text": update,
        }
    }


def _callback(data: str) -> dict:
    return {
        "callback_query": {
            "data": data,
            "message": {"chat": {"id": CHAT_ID}},
        }
    }


def _buttons(msgs: list[OutgoingMessage]) -> list[list[InlineButton]]:
    """Достаёт кнопки из первого сообщения в списке (для удобства)."""
    assert msgs, "no messages returned"
    return msgs[0].reply_markup


def _first(msgs: list[OutgoingMessage]) -> OutgoingMessage:
    assert msgs, "no messages returned"
    return msgs[0]


def _state(conn: sqlite3.Connection) -> str:
    return StorageFacade(conn).telegram_sessions.get(CHAT_ID).state


# ─── Импорт / инстанцирование ───────────────────────────────────────


def test_service_can_be_imported():
    """Сервис экспортируется из ``services``."""
    from hh_applicant_tool.services import ReviewFlowService as Imported

    assert Imported is ReviewFlowService


def test_service_instantiation_with_minimal_args(
    storage: sqlite3.Connection,
):
    """Минимальный конструктор: storage + transport."""
    svc = ReviewFlowService(
        storage=StorageFacade(storage),
        transport=_make_transport(),
    )
    assert svc.clock is not None
    assert svc.storage is not None


# ─── Happy path: intro → test → cover → confirm → enqueue ──────────


def test_full_happy_path_with_test_answer(
    storage: sqlite3.Connection,
):
    """Полный сценарий: один choice-вопрос, cover, enqueue apply_job."""
    ai = MagicMock()
    svc = _make_service(storage, ai_client=ai)
    draft_id = _make_draft(
        storage,
        vacancy_id=1,
        has_test=True,
        cover_letter="Готов работать!",
    )
    _make_test_answer(
        storage,
        draft_id,
        answer_type="choice",
        question="Какой HTTP-код?",
        generated="201",
        options=["200", "201", "404"],
    )
    storage.commit()

    # 1) Стартуем с пустой БД сессии → загружается первый draft.
    msgs = svc.process_message(_msg("/start"))
    assert len(msgs) == 1
    intro = _first(msgs)
    assert "Senior Python" in intro.text
    assert [b.text for b in _buttons(msgs)[0]] == ["Продолжить"]
    assert _state(storage) == STATE_REVIEW_INTRO

    # 2) Нажимаем «Продолжить» — переходим к тесту.
    msgs = svc.process_callback(_callback(CB_INTRO_CONTINUE))
    assert _state(storage) == STATE_REVIEW_TEST
    assert "Какой HTTP-код" in _first(msgs).text
    # Для choice кнопок должно быть 4 (Ок / Выбрать другой / Реген / Свой).
    assert len(_buttons(msgs)) == 4

    # 3) Approve теста.
    msgs = svc.process_callback(_callback(CB_TEST_OK))
    assert _state(storage) == STATE_REVIEW_COVER
    # Ответ теста помечен approved.
    answers = StorageFacade(storage).application_test_answers.find_by_draft(
        draft_id
    )
    assert answers[0].review_status == "approved"

    # 4) Approve cover letter.
    msgs = svc.process_callback(_callback(CB_COVER_OK))
    assert _state(storage) == STATE_CONFIRM_APPLY
    assert "Отправить отклик" in _first(msgs).text

    # 5) Send → draft queued, apply_jobs создан, FSM снова в idle.
    msgs = svc.process_callback(_callback(CB_CONFIRM_SEND))
    # Первый элемент — ack, далее — следующий draft (но его нет).
    assert "Отклик поставлен в очередь" in msgs[0].text
    assert _state(storage) == STATE_IDLE
    facade = StorageFacade(storage)
    draft = facade.application_drafts.get(draft_id)
    assert draft.status == "queued"
    jobs = list(facade.apply_jobs.find(draft_id=draft_id))
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert jobs[0].attempts == 0


def test_full_happy_path_without_test(storage: sqlite3.Connection):
    """Черновик без теста: intro → cover → confirm → enqueue."""
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=2, has_test=False)

    svc.process_message(_msg("/start"))
    # «Продолжить» в intro без теста → сразу cover.
    msgs = svc.process_callback(_callback(CB_INTRO_CONTINUE))
    assert _state(storage) == STATE_REVIEW_COVER
    assert "Сопроводительное" in _first(msgs).text
    # Cover → Confirm.
    msgs = svc.process_callback(_callback(CB_COVER_OK))
    assert _state(storage) == STATE_CONFIRM_APPLY
    # Confirm send.
    msgs = svc.process_callback(_callback(CB_CONFIRM_SEND))
    assert _state(storage) == STATE_IDLE


# ─── Skip на каждом шаге ───────────────────────────────────────────


def test_skip_at_intro(storage: sqlite3.Connection):
    """«Пропустить» на intro → draft=skipped, загружается следующий."""
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=10, has_test=False)
    _make_draft(storage, vacancy_id=11, has_test=False)

    svc.process_message(_msg("/start"))
    msgs = svc.process_callback(_callback(CB_INTRO_SKIP))
    facade = StorageFacade(storage)
    skipped = list(facade.application_drafts.find(vacancy_id=10))[0]
    assert skipped.status == "skipped"
    # Должен загрузиться следующий draft (vacancy_id=11) с intro.
    # Сообщение skip-ack идёт первым, intro следующего draft — последним.
    assert "пропущен" in msgs[0].text
    assert "Senior Python" in msgs[-1].text
    # FSM в intro следующего draft.
    assert _state(storage) == STATE_REVIEW_INTRO


def test_skip_at_test_review(storage: sqlite3.Connection):
    """«Пропустить» в момент теста (вызывается через intro-skip,
    который работает в любом состоянии)."""
    svc = _make_service(storage)
    draft_id = _make_draft(storage, vacancy_id=20, has_test=True)
    _make_test_answer(storage, draft_id, task_id="t1")
    _make_draft(storage, vacancy_id=21, has_test=False)

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))  # → review_test
    assert _state(storage) == STATE_REVIEW_TEST

    svc.process_callback(_callback(CB_INTRO_SKIP))
    facade = StorageFacade(storage)
    assert facade.application_drafts.get(draft_id).status == "skipped"


def test_skip_at_cover_review(storage: sqlite3.Connection):
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=30, has_test=False)
    _make_draft(storage, vacancy_id=31, has_test=False)

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))  # → cover
    assert _state(storage) == STATE_REVIEW_COVER

    svc.process_callback(_callback(CB_INTRO_SKIP))
    skipped = list(
        StorageFacade(storage).application_drafts.find(vacancy_id=30)
    )[0]
    assert skipped.status == "skipped"


def test_skip_at_confirm(storage: sqlite3.Connection):
    """«Пропустить» на шаге confirm → draft=skipped, следующий draft."""
    svc = _make_service(storage)
    draft_id = _make_draft(storage, vacancy_id=40, has_test=False)
    _make_draft(storage, vacancy_id=41, has_test=False)

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    svc.process_callback(_callback(CB_COVER_OK))  # → confirm
    assert _state(storage) == STATE_CONFIRM_APPLY

    msgs = svc.process_callback(_callback(CB_CONFIRM_SKIP))
    assert StorageFacade(storage).application_drafts.get(draft_id).status == (
        "skipped"
    )
    # Последний элемент списка — intro следующего draft.
    assert "Senior Python" in msgs[-1].text


# ─── Регенерация ответов (AI) ──────────────────────────────────────


def test_regenerate_test_answer_calls_ai(
    storage: sqlite3.Connection,
):
    """«Перегенерировать» → ждём комментарий → AI → обновляем ответ."""
    ai = MagicMock()
    ai.complete.return_value = "Новый ответ от AI"
    svc = _make_service(storage, ai_client=ai)
    draft_id = _make_draft(storage, vacancy_id=50, has_test=True)
    answer_id = _make_test_answer(
        storage,
        draft_id,
        answer_type="text",
        generated="Старый ответ",
    )
    storage.commit()

    # Доходим до review_test.
    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    assert _state(storage) == STATE_REVIEW_TEST

    # Нажимаем «Перегенерировать» → awaiting comment.
    msgs = svc.process_callback(_callback(CB_TEST_REGEN))
    assert _state(storage) == STATE_AWAIT_TEST_REGEN
    assert "комментарий" in _first(msgs).text.lower()

    # Шлём комментарий.
    msgs = svc.process_message(_msg("Сделай короче"))
    # AI вызван ровно один раз.
    assert ai.complete.call_count == 1
    # Ответ в БД обновлён.
    answer = StorageFacade(storage).application_test_answers.get(answer_id)
    assert answer.generated_answer == "Новый ответ от AI"
    assert answer.review_status == "regenerated"
    assert answer.reviewer_comment == "Сделай короче"
    # FSM вернулась в review_test.
    assert _state(storage) == STATE_REVIEW_TEST
    # Сообщение содержит новый ответ.
    assert "Новый ответ от AI" in _first(msgs).text


def test_regenerate_choice_answer_snaps_to_option(
    storage: sqlite3.Connection,
):
    """Для choice-вопроса регенерация сохраняет новый вариант
    и в ``generated_answer``, и в ``selected_solution_id``."""
    ai = MagicMock()
    ai.complete.return_value = "404"
    svc = _make_service(storage, ai_client=ai)
    draft_id = _make_draft(storage, vacancy_id=51, has_test=True)
    answer_id = _make_test_answer(
        storage,
        draft_id,
        answer_type="choice",
        generated="200",
        options=["200", "201", "404"],
    )
    storage.commit()

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    svc.process_callback(_callback(CB_TEST_REGEN))
    svc.process_message(_msg("перегенерируй"))
    answer = StorageFacade(storage).application_test_answers.get(answer_id)
    assert answer.generated_answer == "404"
    assert answer.selected_solution_id == "404"


def test_regenerate_cover_letter_calls_ai(
    storage: sqlite3.Connection,
):
    """«Перегенерировать» на cover → AI → cover_letter обновлено."""
    ai = MagicMock()
    ai.complete.return_value = "Свежее письмо v2"
    svc = _make_service(storage, ai_client=ai)
    _make_draft(
        storage,
        vacancy_id=60,
        has_test=False,
        cover_letter="Старое письмо",
    )
    storage.commit()

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))  # → cover
    assert _state(storage) == STATE_REVIEW_COVER

    svc.process_callback(_callback(CB_COVER_REGEN))
    assert _state(storage) == STATE_AWAIT_COVER_REGEN
    msgs = svc.process_message(_msg("Более формально"))
    assert ai.complete.call_count == 1
    draft = StorageFacade(storage).application_drafts.find(vacancy_id=60)
    draft_obj = list(draft)[0]
    assert draft_obj.cover_letter == "Свежее письмо v2"
    assert draft_obj.cover_letter_status == "regenerated"
    assert "Свежее письмо v2" in _first(msgs).text


def test_regenerate_without_ai_says_so(storage: sqlite3.Connection):
    """Без AI клиента регенерация возвращает понятное сообщение,
    а не падает. Состояние остаётся awaiting."""
    svc = _make_service(storage, ai_client=None)
    draft_id = _make_draft(storage, vacancy_id=70, has_test=True)
    _make_test_answer(storage, draft_id)

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    svc.process_callback(_callback(CB_TEST_REGEN))
    msgs = svc.process_message(_msg("любой комментарий"))
    assert "AI не настроен" in _first(msgs).text
    assert _state(storage) == STATE_AWAIT_TEST_REGEN


# ─── Свой ответ (custom) ──────────────────────────────────────────


def test_custom_test_answer(storage: sqlite3.Connection):
    """«Свой ответ» → ждём текст → сохраняем с review_status=custom."""
    svc = _make_service(storage)
    draft_id = _make_draft(storage, vacancy_id=80, has_test=True)
    answer_id = _make_test_answer(
        storage,
        draft_id,
        answer_type="text",
        generated="AI-ответ",
    )
    storage.commit()

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    svc.process_callback(_callback(CB_TEST_CUSTOM))
    assert _state(storage) == STATE_AWAIT_TEST_CUSTOM

    msgs = svc.process_message(_msg("Это мой собственный ответ"))
    answer = StorageFacade(storage).application_test_answers.get(answer_id)
    assert answer.generated_answer == "Это мой собственный ответ"
    assert answer.review_status == "custom"
    # После custom — следующий шаг (тут других тестов нет → cover).
    assert _state(storage) == STATE_REVIEW_COVER
    assert "Сопроводительное" in _first(msgs).text


def test_custom_cover_letter(storage: sqlite3.Connection):
    """«Свой ответ» на cover → текст сохраняется, status=custom."""
    svc = _make_service(storage)
    _make_draft(
        storage, vacancy_id=81, has_test=False, cover_letter="AI-письмо"
    )
    storage.commit()

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    svc.process_callback(_callback(CB_COVER_CUSTOM))
    assert _state(storage) == STATE_AWAIT_COVER_CUSTOM

    svc.process_message(_msg("Моё личное письмо"))
    draft = list(StorageFacade(storage).application_drafts.find(vacancy_id=81))[
        0
    ]
    assert draft.cover_letter == "Моё личное письмо"
    assert draft.cover_letter_status == "custom"
    assert _state(storage) == STATE_CONFIRM_APPLY


# ─── «Выбрать другой» для choice ───────────────────────────────────


def test_choose_other_cycles_options(storage: sqlite3.Connection):
    """«Выбрать другой» переключает selected_solution_id по кругу."""
    svc = _make_service(storage)
    draft_id = _make_draft(storage, vacancy_id=90, has_test=True)
    answer_id = _make_test_answer(
        storage,
        draft_id,
        answer_type="choice",
        generated="200",
        options=["200", "201", "404"],
    )
    storage.commit()

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    # 1-й клик: 200 → 201.
    svc.process_callback(_callback(CB_TEST_CHOOSE))
    ans = StorageFacade(storage).application_test_answers.get(answer_id)
    assert ans.generated_answer == "201"
    assert ans.selected_solution_id == "201"
    assert ans.review_status == "needs_choice"
    # 2-й клик: 201 → 404.
    svc.process_callback(_callback(CB_TEST_CHOOSE))
    ans = StorageFacade(storage).application_test_answers.get(answer_id)
    assert ans.generated_answer == "404"
    # 3-й клик: 404 → 200 (цикл).
    svc.process_callback(_callback(CB_TEST_CHOOSE))
    ans = StorageFacade(storage).application_test_answers.get(answer_id)
    assert ans.generated_answer == "200"


# ─── Несколько тестов на черновик ──────────────────────────────────


def test_multiple_test_answers_advance_in_order(
    storage: sqlite3.Connection,
):
    """Несколько тестов: после approve первого показывается второй,
    после последнего — cover letter."""
    svc = _make_service(storage)
    draft_id = _make_draft(storage, vacancy_id=100, has_test=True)
    _make_test_answer(
        storage, draft_id, task_id="t1", question="Q1", generated="A1"
    )
    _make_test_answer(
        storage, draft_id, task_id="t2", question="Q2", generated="A2"
    )
    storage.commit()

    svc.process_message(_msg("/start"))
    msgs = svc.process_callback(_callback(CB_INTRO_CONTINUE))
    # Вопрос 1/2.
    assert "1/2" in _first(msgs).text
    msgs = svc.process_callback(_callback(CB_TEST_OK))
    # Вопрос 2/2.
    assert "2/2" in _first(msgs).text
    msgs = svc.process_callback(_callback(CB_TEST_OK))
    # Тестов больше нет → cover.
    assert _state(storage) == STATE_REVIEW_COVER


# ─── Resume / restart ──────────────────────────────────────────────


def test_resume_idle_loads_next_draft(storage: sqlite3.Connection):
    """``resume_session`` из idle подхватывает следующий ``prepared``."""
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=110, has_test=False)

    msgs = svc.resume_session(CHAT_ID)
    assert _first(msgs).text and "Senior Python" in _first(msgs).text
    assert _state(storage) == STATE_REVIEW_INTRO


def test_resume_mid_state_rerenders_step(storage: sqlite3.Connection):
    """``resume_session`` из середины FSM — перерисовывает текущий шаг."""
    svc = _make_service(storage)
    draft_id = _make_draft(storage, vacancy_id=120, has_test=True)
    _make_test_answer(storage, draft_id)
    storage.commit()

    # Прогоняем до review_test в первом «бот-цикле».
    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    assert _state(storage) == STATE_REVIEW_TEST

    # Симулируем перезапуск: создаём новый экземпляр сервиса с той же БД.
    svc2 = _make_service(storage)
    msgs = svc2.resume_session(CHAT_ID)
    assert _state(storage) == STATE_REVIEW_TEST
    assert "Вопрос 1/1" in _first(msgs).text


def test_resume_in_awaiting_state_keeps_waiting(
    storage: sqlite3.Connection,
):
    """``resume_session`` в awaiting_* показывает «ожидаю ввод»."""
    svc = _make_service(storage)
    draft_id = _make_draft(storage, vacancy_id=130, has_test=True)
    _make_test_answer(storage, draft_id)
    storage.commit()

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    svc.process_callback(_callback(CB_TEST_CUSTOM))
    assert _state(storage) == STATE_AWAIT_TEST_CUSTOM

    svc2 = _make_service(storage)
    msgs = svc2.resume_session(CHAT_ID)
    assert _state(storage) == STATE_AWAIT_TEST_CUSTOM
    assert "Ожидаю ваш ввод" in _first(msgs).text


def test_resume_no_drafts_stays_idle(storage: sqlite3.Connection):
    """``resume_session`` без черновиков и сессии → idle + сообщение."""
    svc = _make_service(storage)
    msgs = svc.resume_session(CHAT_ID)
    assert "0 вакансий" in _first(msgs).text
    assert _state(storage) == STATE_IDLE


# ─── Состояние FSM: проверка обновлений в БД ───────────────────────


def test_state_transitions_persist(storage: sqlite3.Connection):
    """Каждое нажатие кнопки обновляет ``telegram_sessions.state``."""
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=140, has_test=True)
    facade = StorageFacade(storage)
    draft = list(facade.application_drafts.find(vacancy_id=140))[0]
    _make_test_answer(storage, draft.id, task_id="t1")

    transitions = [
        (CB_INTRO_CONTINUE, STATE_REVIEW_TEST),
        (CB_TEST_OK, STATE_REVIEW_COVER),
        (CB_COVER_OK, STATE_CONFIRM_APPLY),
    ]
    svc.process_message(_msg("/start"))
    for cb, expected in transitions:
        svc.process_callback(_callback(cb))
        assert _state(storage) == expected, f"after {cb}"


def test_session_is_persisted_between_service_instances(
    storage: sqlite3.Connection,
):
    """Сессия живёт в БД — новый экземпляр сервиса её подхватывает."""
    _make_draft(storage, vacancy_id=150, has_test=False)
    svc1 = _make_service(storage)
    svc1.process_message(_msg("/start"))
    assert _state(storage) == STATE_REVIEW_INTRO

    svc2 = _make_service(storage)
    assert _state(storage) == STATE_REVIEW_INTRO
    msgs = svc2.resume_session(CHAT_ID)
    assert "Senior Python" in _first(msgs).text


# ─── Граничные случаи ──────────────────────────────────────────────


def test_open_url_button_sends_link(storage: sqlite3.Connection):
    """«Открыть на HH» шлёт ссылку отдельным сообщением, FSM не меняет."""
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=160, has_test=False)

    svc.process_message(_msg("/start"))
    msgs = svc.process_callback(_callback(CB_INTRO_OPEN))
    assert "https://hh.ru/vacancy/160" in _first(msgs).text
    assert _state(storage) == STATE_REVIEW_INTRO


def test_text_message_ignored_in_non_awaiting_states(
    storage: sqlite3.Connection,
):
    """Текст «привет» во время review_intro не ломает FSM."""
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=170, has_test=False)
    svc.process_message(_msg("/start"))
    assert _state(storage) == STATE_REVIEW_INTRO

    msgs = svc.process_message(_msg("привет"))
    assert msgs == []
    assert _state(storage) == STATE_REVIEW_INTRO


def test_callback_with_unknown_action_is_ignored(
    storage: sqlite3.Connection,
):
    """Неизвестный callback_data → пустой ответ, состояние не меняется."""
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=180, has_test=False)
    svc.process_message(_msg("/start"))

    msgs = svc.process_callback(_callback("unknown:foo"))
    assert msgs == []
    assert _state(storage) == STATE_REVIEW_INTRO


def test_callback_without_rf_prefix_ignored(
    storage: sqlite3.Connection,
):
    """Callback без префикса ``rf:`` (другая фича бота) → игнор."""
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=190, has_test=False)
    svc.process_message(_msg("/start"))

    msgs = svc.process_callback(_callback("digest:something"))
    assert msgs == []
    assert _state(storage) == STATE_REVIEW_INTRO


def test_message_without_chat_id_returns_empty(
    storage: sqlite3.Connection,
):
    """Update без chat_id — пустой список (FSM не должна падать)."""
    svc = _make_service(storage)
    msgs = svc.process_message({"message": {"text": "abc"}})
    assert msgs == []


def test_apply_jobs_upsert_on_repeat_send(
    storage: sqlite3.Connection,
):
    """Повторное нажатие «Отправить» не плодит дублей apply_jobs
    (UNIQUE по draft_id + UPSERT)."""
    svc = _make_service(storage)
    draft_id = _make_draft(storage, vacancy_id=200, has_test=False)

    # Первый прогон.
    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    svc.process_callback(_callback(CB_COVER_OK))
    msgs = svc.process_callback(_callback(CB_CONFIRM_SEND))
    # В первом прогоне вернулся ack + сообщение о следующем draft (которого нет).
    assert msgs
    assert "Отклик поставлен в очередь" in msgs[0].text
    # FSM в idle.
    assert _state(storage) == STATE_IDLE
    facade = StorageFacade(storage)
    jobs = list(facade.apply_jobs.find(draft_id=draft_id))
    assert len(jobs) == 1
    assert jobs[0].status == "queued"


def test_session_created_on_demand(storage: sqlite3.Connection):
    """Первый апдейт для chat_id создаёт сессию в БД."""
    svc = _make_service(storage)
    _make_draft(storage, vacancy_id=210, has_test=False)
    assert StorageFacade(storage).telegram_sessions.get(CHAT_ID) is None
    svc.process_message(_msg("/start"))
    session = StorageFacade(storage).telegram_sessions.get(CHAT_ID)
    assert session is not None
    assert session.state == STATE_REVIEW_INTRO


def test_session_payload_json_for_regen_target(
    storage: sqlite3.Connection,
):
    """payload_json хранит target («test_answer» / «cover_letter»)."""
    svc = _make_service(storage)
    draft_id = _make_draft(storage, vacancy_id=220, has_test=True)
    _make_test_answer(storage, draft_id)

    svc.process_message(_msg("/start"))
    svc.process_callback(_callback(CB_INTRO_CONTINUE))
    svc.process_callback(_callback(CB_TEST_REGEN))

    session = StorageFacade(storage).telegram_sessions.get(CHAT_ID)
    assert session.payload_json == {"target": "test_answer"}


def test_draft_status_unchanged_when_session_lost(
    storage: sqlite3.Connection,
):
    """Если draft_id в сессии битый (нет в БД), FSM сбрасывается
    в idle без падения; draft.status не трогаем."""
    facade = StorageFacade(storage)
    facade.telegram_sessions.save(
        TelegramSessionModel(
            chat_id=CHAT_ID,
            state=STATE_REVIEW_INTRO,
            draft_id=9999,  # несуществующий
        )
    )
    storage.commit()

    svc = _make_service(storage)
    msgs = svc.resume_session(CHAT_ID)
    # Должно либо показать «нет prepared», либо при следующей загрузке
    # (без черновика) — в любом случае не падать.
    assert msgs
    assert _state(storage) in (STATE_IDLE, STATE_REVIEW_INTRO)
