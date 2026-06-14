"""Тесты ежедневного Telegram-дайджеста (issue #8).

Сервис тестируется целиком на in-memory SQLite (``StorageFacade`` + фикстура
``storage``) с моком :class:`TelegramTransport`. ``Clock`` подменяется
фиксированной датой, чтобы тесты были детерминированными вне зависимости
от того, в какой день их запускают.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.ai.base import AIError
from hh_applicant_tool.services.daily_digest import (
    LAST_DIGEST_KEY,
    DailyDigestService,
    DigestResult,
    DraftGroup,
)
from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.application_draft import (
    ApplicationDraftModel,
)
from hh_applicant_tool.storage.models.search_profile import SearchProfileModel
from hh_applicant_tool.storage.models.setting import SettingModel
from job_bot.telegram_bot.telegram_transport import (
    TelegramTransport,
    TelegramTransportError,
)

# ─── Фикстуры и хелперы ─────────────────────────────────────────────


class _FixedClock:
    """Детерминированные часы для тестов.

    Реализует каноничный ``application.ports.Clock`` (требует и ``now``,
    и ``sleep``). ``sleep`` — no-op: тестам не нужна реальная задержка.
    """

    def __init__(self, day: date) -> None:
        self._now = datetime(day.year, day.month, day.day, 9, 0, 0)

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        return None


def _make_transport() -> MagicMock:
    """Мок :class:`TelegramTransport` с дефолтным ответом ``send_message``."""
    transport = MagicMock(spec=TelegramTransport)
    transport.send_message.return_value = {"message_id": 1, "ok": True}
    return transport


def _make_service(
    conn: sqlite3.Connection,
    *,
    transport: MagicMock | None = None,
    config: dict | None = None,
    day: date = date(2026, 6, 9),
    ai_client: MagicMock | None = None,
) -> DailyDigestService:
    """Собирает сервис с дефолтным конфигом (chat_id=42) и фикс. часами."""
    facade = StorageFacade(conn)
    if config is None:
        config = {"telegram": {"digest_chat_id": 42}}
    return DailyDigestService(
        storage=facade,
        transport=transport if transport is not None else _make_transport(),
        config=config,
        clock=_FixedClock(day),
        ai_client=ai_client,
    )


def _save_profile(
    facade: StorageFacade, pid: str, name: str, enabled: bool = True
) -> None:
    facade.search_profiles.save(
        SearchProfileModel(id=pid, name=name, resume_id="r1", enabled=enabled)
    )


def _save_draft(
    facade: StorageFacade,
    *,
    profile_id: str | None,
    resume_id: str,
    vacancy_id: int,
    status: str = "prepared",
    has_test: bool = False,
    relevance_score: int | None = None,
) -> None:
    facade.application_drafts.save(
        ApplicationDraftModel(
            search_profile_id=profile_id,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            status=status,
            has_test=has_test,
            relevance_score=relevance_score,
        )
    )


# ─── Импорт / инстанцирование ───────────────────────────────────────


def test_service_can_be_imported():
    """Сервис экспортируется из ``services`` и принимает DI-аргументы."""
    from hh_applicant_tool.services import (
        DailyDigestService as Imported,
    )

    assert Imported is DailyDigestService


def test_service_instantiation_with_minimal_args(storage: sqlite3.Connection):
    """Минимальный конструктор: storage + transport (config/clock — None)."""
    svc = DailyDigestService(
        storage=StorageFacade(storage),
        transport=_make_transport(),
    )
    assert svc.clock is not None  # fallback SystemClock


# ─── Группировка и агрегация ────────────────────────────────────────


def test_collect_groups_empty(storage: sqlite3.Connection):
    """Пустая БД → пустой список групп."""
    svc = _make_service(storage)
    assert svc.collect_groups() == []


def test_collect_groups_ignores_non_prepared(storage: sqlite3.Connection):
    """Учитываем только ``status='prepared'`` — остальные игнорируются."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "Profile 1")
    _save_draft(
        facade,
        profile_id="p1",
        resume_id="r1",
        vacancy_id=1,
        status="prepared",
    )
    _save_draft(
        facade,
        profile_id="p1",
        resume_id="r2",
        vacancy_id=2,
        status="rejected",
    )
    _save_draft(
        facade,
        profile_id="p1",
        resume_id="r3",
        vacancy_id=3,
        status="approved",
    )
    storage.commit()

    groups = _make_service(storage).collect_groups()
    assert len(groups) == 1
    assert groups[0].total == 1


def test_collect_groups_groups_by_profile_and_splits_tests(
    storage: sqlite3.Connection,
):
    """Группировка по профилю + разбивка ``has_test`` / без."""
    facade = StorageFacade(storage)
    _save_profile(facade, "django", "Django Senior")
    _save_profile(facade, "fastapi", "FastAPI")

    # Django: 3 всего, 1 с тестом, 2 без
    _save_draft(
        facade,
        profile_id="django",
        resume_id="r1",
        vacancy_id=10,
        has_test=True,
        relevance_score=90,
    )
    _save_draft(
        facade,
        profile_id="django",
        resume_id="r1",
        vacancy_id=11,
        has_test=False,
        relevance_score=80,
    )
    _save_draft(
        facade,
        profile_id="django",
        resume_id="r1",
        vacancy_id=12,
        has_test=False,
        relevance_score=70,
    )
    # FastAPI: 1 без теста
    _save_draft(
        facade,
        profile_id="fastapi",
        resume_id="r2",
        vacancy_id=20,
        has_test=False,
        relevance_score=60,
    )
    storage.commit()

    groups = _make_service(storage).collect_groups()
    by_pid = {g.search_profile_id: g for g in groups}

    django = by_pid["django"]
    assert django.total == 3
    assert django.with_tests == 1
    assert django.without_tests == 2
    assert django.average_score == 80  # (90+80+70)/3 = 80
    assert django.profile_name == "Django Senior"

    fastapi = by_pid["fastapi"]
    assert fastapi.total == 1
    assert fastapi.with_tests == 0
    assert fastapi.without_tests == 1
    assert fastapi.average_score == 60
    assert fastapi.profile_name == "FastAPI"


def test_collect_groups_profile_name_falls_back_to_id(
    storage: sqlite3.Connection,
):
    """Если профиля нет в ``search_profiles`` — показываем его id."""
    facade = StorageFacade(storage)
    _save_draft(
        facade,
        profile_id="unknown",
        resume_id="r1",
        vacancy_id=1,
    )
    storage.commit()

    groups = _make_service(storage).collect_groups()
    assert len(groups) == 1
    assert groups[0].profile_name == "unknown"


def test_collect_groups_profile_none_label(storage: sqlite3.Connection):
    """Черновики без ``search_profile_id`` попадают в группу ``(без профиля)``."""
    facade = StorageFacade(storage)
    _save_draft(
        facade,
        profile_id=None,
        resume_id="r1",
        vacancy_id=1,
    )
    storage.commit()

    groups = _make_service(storage).collect_groups()
    assert len(groups) == 1
    assert groups[0].search_profile_id is None
    assert groups[0].profile_name == "(без профиля)"


def test_collect_groups_avg_score_none_when_no_scores(
    storage: sqlite3.Connection,
):
    """Если ни у одного черновика нет ``relevance_score`` — avg=None."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(
        facade,
        profile_id="p1",
        resume_id="r1",
        vacancy_id=1,
        relevance_score=None,
    )
    storage.commit()

    groups = _make_service(storage).collect_groups()
    assert groups[0].average_score is None


def test_collect_groups_sorted_by_total_desc(
    storage: sqlite3.Connection,
):
    """Группы сортируются по убыванию ``total`` (самые «жирные» сверху)."""
    facade = StorageFacade(storage)
    _save_profile(facade, "a", "AAA")
    _save_profile(facade, "b", "BBB")
    _save_draft(facade, profile_id="a", resume_id="r1", vacancy_id=1)
    for i in range(3):
        _save_draft(facade, profile_id="b", resume_id="r1", vacancy_id=10 + i)
    storage.commit()

    groups = _make_service(storage).collect_groups()
    assert [g.search_profile_id for g in groups] == ["b", "a"]


# ─── Форматирование ────────────────────────────────────────────────


def test_format_message_matches_issue_example():
    """Формат соответствует примеру из issue #8."""
    groups = [
        DraftGroup(
            search_profile_id="django-senior",
            profile_name="Django Senior",
            total=8,
            with_tests=3,
            without_tests=5,
            average_score=88,
        ),
        DraftGroup(
            search_profile_id="fastapi",
            profile_name="FastAPI",
            total=4,
            with_tests=1,
            without_tests=3,
            average_score=74,
        ),
        DraftGroup(
            search_profile_id="n8n",
            profile_name="Automation / n8n",
            total=5,
            with_tests=0,
            without_tests=5,
            average_score=81,
        ),
    ]

    text = DailyDigestService.format_message(groups, total=17)

    assert text.startswith("Доброе утро! ☀️\n")
    assert "Готово к ревью: 17 вакансий" in text
    assert "Django Senior:" in text
    assert "• новых: 8 (с тестами: 3, без: 5)" in text
    assert "• средний score: 88" in text
    assert "FastAPI:" in text
    assert "• новых: 4 (с тестами: 1, без: 3)" in text
    assert "Automation / n8n:" in text


def test_format_message_empty_groups_returns_short_notice():
    """Без групп — короткое уведомление, никакой таблицы."""
    text = DailyDigestService.format_message([], total=0)
    assert "Доброе утро" in text
    assert "Сегодня нет подготовленных черновиков" in text
    # В коротком уведомлении не должно быть «Готово к ревью»
    assert "Готово к ревью" not in text


def test_format_message_omits_score_when_none():
    """Строка ``средний score`` пропускается, если avg=None."""
    groups = [
        DraftGroup(
            search_profile_id="p1",
            profile_name="P1",
            total=2,
            with_tests=0,
            without_tests=2,
            average_score=None,
        )
    ]
    text = DailyDigestService.format_message(groups, total=2)
    assert "средний score" not in text
    assert "P1:" in text


def test_format_message_includes_ai_summary():
    """AI-аннотация вставляется отдельной строкой ``💡 …``."""
    groups = [
        DraftGroup(
            search_profile_id="p1",
            profile_name="P1",
            total=3,
            with_tests=1,
            without_tests=2,
            average_score=80,
        )
    ]
    text = DailyDigestService.format_message(
        groups, total=3, ai_summary="Сегодня много интересных вакансий"
    )
    assert "💡 Сегодня много интересных вакансий" in text


# ─── Idempotency ────────────────────────────────────────────────────


def test_already_sent_today_no_record(storage: sqlite3.Connection):
    """Без записи в ``settings`` — не отправлялось."""
    svc = _make_service(storage)
    assert svc.already_sent_today() is False


def test_already_sent_today_true_after_mark(storage: sqlite3.Connection):
    """После ``_mark_sent_today`` — возвращает ``True``."""
    svc = _make_service(storage)
    today = svc.today()
    svc._mark_sent_today(today)
    storage.commit()
    assert svc.already_sent_today(today) is True


def test_already_sent_today_false_for_different_day(
    storage: sqlite3.Connection,
):
    """Вчерашняя дата в ``settings`` не блокирует сегодняшнюю отправку."""
    svc = _make_service(storage)
    yesterday = date(2026, 6, 8)
    svc._mark_sent_today(yesterday)
    storage.commit()
    assert svc.already_sent_today(date(2026, 6, 9)) is False


def test_already_sent_today_uses_settings_key(
    storage: sqlite3.Connection,
):
    """Ключ idempotency-флага — именно ``telegram.last_digest_date``."""
    facade = StorageFacade(storage)
    facade.settings.save(SettingModel(key=LAST_DIGEST_KEY, value="2026-06-09"))
    storage.commit()

    svc = _make_service(storage)
    assert svc.already_sent_today(date(2026, 6, 9)) is True


# ─── send() — happy path и error cases ─────────────────────────────


def test_send_first_time_sends_and_marks_settings(
    storage: sqlite3.Connection,
):
    """Первая отправка за день: шлёт сообщение и сохраняет дату."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(facade, profile_id="p1", resume_id="r1", vacancy_id=1)
    storage.commit()

    transport = _make_transport()
    svc = _make_service(storage, transport=transport)
    result = svc.send()

    assert isinstance(result, DigestResult)
    assert result.sent is True
    assert result.skipped_reason is None
    assert result.total_drafts == 1
    assert "Готово к ревью: 1 вакансий" in result.message

    transport.send_message.assert_called_once()
    chat_id, text = transport.send_message.call_args.args
    assert chat_id == 42
    assert "Доброе утро" in text

    storage.commit()
    assert facade.settings.get_value(LAST_DIGEST_KEY) == "2026-06-09"


def test_send_already_sent_today_skips(storage: sqlite3.Connection):
    """Повторный ``send()`` в тот же день — no-op, transport не дёргается."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(facade, profile_id="p1", resume_id="r1", vacancy_id=1)
    facade.settings.save(SettingModel(key=LAST_DIGEST_KEY, value="2026-06-09"))
    storage.commit()

    transport = _make_transport()
    svc = _make_service(storage, transport=transport)
    result = svc.send()

    assert result.sent is False
    assert result.skipped_reason == "already_sent"
    transport.send_message.assert_not_called()


def test_send_force_bypasses_idempotency(storage: sqlite3.Connection):
    """``force=True`` шлёт повторно, даже если сегодня уже отправляли."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(facade, profile_id="p1", resume_id="r1", vacancy_id=1)
    facade.settings.save(SettingModel(key=LAST_DIGEST_KEY, value="2026-06-09"))
    storage.commit()

    transport = _make_transport()
    svc = _make_service(storage, transport=transport)
    result = svc.send(force=True)

    assert result.sent is True
    transport.send_message.assert_called_once()


def test_send_empty_db_sends_short_notice(storage: sqlite3.Connection):
    """Пустая БД → отправляем короткое уведомление, а не молчим."""
    transport = _make_transport()
    svc = _make_service(storage, transport=transport)
    result = svc.send()

    assert result.sent is True
    assert result.total_drafts == 0
    transport.send_message.assert_called_once()
    chat_id, text = transport.send_message.call_args.args
    assert "Сегодня нет подготовленных черновиков" in text
    assert "Готово к ревью" not in text  # в коротком уведомлении этого нет


@pytest.mark.parametrize(
    "config,expected_sent,expected_skipped_reason,expected_chat_id",
    [
        # Error cases
        ({"other_section": {}}, False, "no_telegram_config", None),
        ({"telegram": {"bot_token": "x"}}, False, "no_chat_id", None),
        # Success cases with chat_id resolution
        ({"telegram": {"allowed_user_ids": [777, 888]}}, True, None, 777),
        (
            {"telegram": {"chat_id": 42, "allowed_user_ids": [999]}},
            True,
            None,
            42,
        ),
        (
            {
                "telegram": {
                    "digest_chat_id": 1,
                    "chat_id": 2,
                    "allowed_user_ids": [3],
                }
            },
            True,
            None,
            1,
        ),
    ],
)
def test_send_chat_id_resolution(
    storage: sqlite3.Connection,
    config: dict,
    expected_sent: bool,
    expected_skipped_reason: str | None,
    expected_chat_id: int | None,
):
    """Chat ID resolution: error cases skip, success cases use fallback order."""
    transport = _make_transport()
    svc = _make_service(storage, transport=transport, config=config)
    result = svc.send()

    assert result.sent is expected_sent
    assert result.skipped_reason == expected_skipped_reason

    if expected_sent:
        transport.send_message.assert_called_once()
        chat_id = transport.send_message.call_args.args[0]
        assert chat_id == expected_chat_id
    else:
        transport.send_message.assert_not_called()


def test_send_telegram_error_does_not_mark_settings(
    storage: sqlite3.Connection,
):
    """Сбой отправки: settings НЕ обновляются, чтобы можно было retry."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(facade, profile_id="p1", resume_id="r1", vacancy_id=1)
    storage.commit()

    transport = _make_transport()
    transport.send_message.side_effect = TelegramTransportError("boom")
    svc = _make_service(storage, transport=transport)
    result = svc.send()

    assert result.sent is False
    assert result.skipped_reason == "send_failed"
    # Message всё равно сформирован — отдаём его в result для диагностики.
    assert "Готово к ревью" in result.message

    # Ключевая проверка: settings НЕ помечены.
    storage.commit()
    assert facade.settings.get_value(LAST_DIGEST_KEY) is None


def test_send_telegram_error_allows_same_day_retry(
    storage: sqlite3.Connection,
):
    """``TelegramTransportError``: ``result`` заполнен, idempotency не
    срабатывает — повторный ``send()`` в тот же день снова дёргает transport.
    """
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(facade, profile_id="p1", resume_id="r1", vacancy_id=1)
    _save_draft(facade, profile_id="p1", resume_id="r1", vacancy_id=2)
    storage.commit()

    transport = _make_transport()
    transport.send_message.side_effect = TelegramTransportError("boom")
    svc = _make_service(storage, transport=transport)
    result = svc.send()

    # 1) ``result`` корректно отражает сбой, но содержит полезные поля.
    assert result.sent is False
    assert result.skipped_reason == "send_failed"
    assert result.total_drafts == 2  # оба черновика учтены
    assert "Готово к ревью" in result.message  # сообщение сформировано
    storage.commit()
    # 2) settings НЕ помечены — флаг last_digest_date не записан.
    assert facade.settings.get_value(LAST_DIGEST_KEY) is None

    # 3) Повторный вызов в тот же день БЕЗ ``force=True`` НЕ скипается
    #    по idempotency (потому что флаг не выставлен) и снова дёргает
    #    transport — имитируем успех и убеждаемся, что путь не «залип».
    transport.send_message.side_effect = None
    transport.send_message.return_value = {"message_id": 2, "ok": True}
    result2 = svc.send()
    assert result2.sent is True
    assert result2.skipped_reason is None
    # transport был дёрнут 2 раза: 1-я попытка (сбой) + повтор (успех).
    assert transport.send_message.call_count == 2
    # И только теперь settings помечены.
    storage.commit()
    assert facade.settings.get_value(LAST_DIGEST_KEY) == "2026-06-09"


# ─── AI-аннотация (опционально) ────────────────────────────────────


def test_send_with_ai_client_appends_summary(storage: sqlite3.Connection):
    """Если AI-клиент передан и отвечает — аннотация попадает в сообщение."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(facade, profile_id="p1", resume_id="r1", vacancy_id=1)
    storage.commit()

    ai = MagicMock()
    ai.complete.return_value = "Топ-1 профиль по Django"
    transport = _make_transport()
    svc = _make_service(storage, transport=transport, ai_client=ai)
    result = svc.send()

    assert result.sent is True
    assert ai.complete.called
    assert "💡 Топ-1 профиль по Django" in result.message


def test_send_ai_failure_does_not_break_send(storage: sqlite3.Connection):
    """Сбой AI не должен ломать отправку дайджеста (AI — декорация)."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(facade, profile_id="p1", resume_id="r1", vacancy_id=1)
    storage.commit()

    ai = MagicMock()
    ai.complete.side_effect = AIError("ai down")
    transport = _make_transport()
    svc = _make_service(storage, transport=transport, ai_client=ai)

    result = svc.send()
    assert result.sent is True
    # Сообщение есть, но без AI-аннотации
    assert "💡" not in result.message


def test_send_ai_not_called_for_empty_db(storage: sqlite3.Connection):
    """AI-аннотация бессмысленна для пустой БД — клиент не дёргаем."""
    ai = MagicMock()
    transport = _make_transport()
    svc = _make_service(storage, transport=transport, ai_client=ai)
    svc.send()
    ai.complete.assert_not_called()


# ─── Параметризация и краевые случаи ────────────────────────────────


@pytest.mark.parametrize("status", ["rejected", "approved", "sent", "new"])
def test_collect_groups_excludes_other_statuses(
    storage: sqlite3.Connection, status: str
):
    """Только ``prepared`` идёт в дайджест, остальные — нет."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(
        facade,
        profile_id="p1",
        resume_id="r1",
        vacancy_id=1,
        status=status,
    )
    storage.commit()

    groups = _make_service(storage).collect_groups()
    assert groups == []


def test_send_idempotency_only_today(storage: sqlite3.Connection):
    """``force=True`` вчерашней датой — не сработает, проверим через clock."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "P1")
    _save_draft(facade, profile_id="p1", resume_id="r1", vacancy_id=1)
    facade.settings.save(
        SettingModel(key=LAST_DIGEST_KEY, value="2026-06-08")  # вчера
    )
    storage.commit()

    transport = _make_transport()
    svc = _make_service(storage, transport=transport)  # clock=2026-06-09
    result = svc.send()

    assert result.sent is True  # не "already_sent" — другой день
    assert transport.send_message.call_args is not None


def test_send_message_total_reflects_all_groups(
    storage: sqlite3.Connection,
):
    """Сумма в шапке совпадает с суммой ``total`` по всем группам."""
    facade = StorageFacade(storage)
    _save_profile(facade, "a", "AAA")
    _save_profile(facade, "b", "BBB")
    _save_draft(facade, profile_id="a", resume_id="r1", vacancy_id=1)
    for i in range(5):
        _save_draft(facade, profile_id="b", resume_id="r1", vacancy_id=10 + i)
    storage.commit()

    transport = _make_transport()
    svc = _make_service(storage, transport=transport)
    result = svc.send()
    assert "Готово к ревью: 6 вакансий" in result.message
