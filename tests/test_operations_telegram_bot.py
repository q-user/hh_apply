"""Тесты CLI-операции ``telegram-bot`` (issue #7).

Покрывает:
* CLI-флаги ``--once`` и ``--send-digest-now`` (argparse);
* новые команды ``/stats``, ``/review``, ``/cancel``;
* режим ``--once`` (один цикл polling → выход);
* флаг ``--send-digest-now`` (force=True → реальный ``send``);
* идемпотентность дайджеста (повторный ``send`` в тот же день — no-op).

``DailyDigestService`` инжектируется через ``Operation._digest_service``,
чтобы не дёргать реальный ``TelegramTransport`` и не упираться в сеть.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, Mock

import pytest

from hh_applicant_tool.operations.telegram_bot import Operation
from hh_applicant_tool.services.daily_digest import DigestResult, DraftGroup
from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.application_draft import (
    ApplicationDraftModel,
)
from hh_applicant_tool.storage.models.search_profile import SearchProfileModel
from hh_applicant_tool.telegram.transport import (
    TelegramTransport,
    TelegramTransportConfig,
)

# ─── Хелперы ─────────────────────────────────────────────────────────


def _make_transport(
    allowed: tuple[int, ...] = (123,),
    updates: list | None = None,
) -> TelegramTransport:
    """Реальный ``TelegramTransport`` без сети; ``get_updates`` мокается."""
    config = TelegramTransportConfig(
        bot_token="test-token",
        poll_timeout=30,
        allowed_user_ids=allowed,
    )
    transport = TelegramTransport(config=config)
    if updates is not None:
        transport.get_updates = Mock(return_value=updates)  # type: ignore[method-assign]
    return transport


@pytest.fixture
def mock_telegram_transport(monkeypatch: pytest.MonkeyPatch):
    """Подменяет ``TelegramTransport`` в модуле бота на мок.

    Нужно для тестов ``Operation.run``: бот сам инстанцирует
    ``TelegramTransport(config=...)``, и без подмены класса мы улетим
    в реальный HTTP-вызов.
    """
    from hh_applicant_tool.operations import telegram_bot as bot_mod

    created: list[MagicMock] = []

    def _factory(*, config):  # noqa: ARG001
        mock = MagicMock(spec=TelegramTransport)
        mock.config = config
        mock.allowed_user_ids = config.allowed_user_ids
        mock.poll_timeout = config.poll_timeout
        mock.get_updates = Mock(return_value=[])
        mock.send_message = Mock()
        created.append(mock)
        return mock

    monkeypatch.setattr(bot_mod, "TelegramTransport", _factory)
    return created


def _build_tool(
    storage_conn: sqlite3.Connection,
    *,
    digest_chat_id: int = 42,
    digest_time: str = "10:00",
    bot_token: str = "test-token",
) -> MagicMock:
    """Минимальный мок ``HHApplicantTool``.

    ``.config`` — обычный dict (как и в проде: ``Config(dict-subclass)``),
    ``.storage`` — настоящая :class:`StorageFacade` поверх фикстуры.
    """
    tool = MagicMock()
    tool.config = {
        "telegram": {
            "bot_token": bot_token,
            "poll_timeout": 30,
            "allowed_user_ids": [123],
            "digest_chat_id": digest_chat_id,
            "daily_digest_time": digest_time,
        },
    }
    tool.storage = StorageFacade(storage_conn)
    return tool


def _save_profile(facade: StorageFacade, pid: str, name: str) -> None:
    facade.search_profiles.save(
        SearchProfileModel(
            id=pid,
            name=name,
            resume_id="r1",
            enabled=True,
        ),
    )


def _save_draft(
    facade: StorageFacade,
    *,
    profile_id: str | None,
    vacancy_id: int,
    has_test: bool = False,
    relevance_score: int | None = None,
    status: str = "prepared",
) -> None:
    facade.application_drafts.save(
        ApplicationDraftModel(
            search_profile_id=profile_id,
            resume_id="r1",
            vacancy_id=vacancy_id,
            status=status,
            has_test=has_test,
            relevance_score=relevance_score,
        ),
    )


def _make_update(text: str, user_id: int = 123, chat_id: int = 456) -> dict:
    return {
        "update_id": 1,
        "message": {
            "from": {"id": user_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def _make_digest_mock(
    *,
    sent: bool = True,
    total_drafts: int = 0,
    skipped_reason: str | None = None,
    groups: list[DraftGroup] | None = None,
    already_sent: bool = False,
) -> MagicMock:
    """Мок ``DailyDigestService`` с дефолтным поведением."""
    digest = MagicMock()
    digest.send.return_value = DigestResult(
        sent=sent,
        skipped_reason=skipped_reason,
        total_drafts=total_drafts,
        message="",
    )
    digest.collect_groups.return_value = groups if groups is not None else []
    digest.already_sent_today.return_value = already_sent
    return digest


def _build_op_with_real_digest(
    storage: sqlite3.Connection,
) -> Operation:
    """Операция с настоящим ``DailyDigestService`` поверх in-memory БД.

    Удобно для тестов, где хочется видеть реальные данные из ``collect_groups``
    (без ручного заполнения ``DraftGroup``).
    """
    op = Operation()
    transport = _make_transport()
    op._digest_service = op._build_digest_service(  # type: ignore[attr-defined]
        _build_tool(storage),
        transport,
    )
    return op


# ─── CLI: парсинг аргументов ─────────────────────────────────────────


class _ParserHost:
    """Минимальный хост для :meth:`Operation.setup_parser`."""

    def __init__(self) -> None:
        self.parser = argparse.ArgumentParser()
        Operation().setup_parser(self.parser)


def test_cli_flag_once_is_store_true() -> None:
    """``--once`` — булев флаг, по умолчанию False."""
    host = _ParserHost()
    args = host.parser.parse_args([])
    assert args.once is False

    args = host.parser.parse_args(["--once"])
    assert args.once is True


def test_cli_flag_send_digest_now_is_store_true() -> None:
    """``--send-digest-now`` — булев флаг, по умолчанию False."""
    host = _ParserHost()
    args = host.parser.parse_args([])
    assert args.send_digest_now is False

    args = host.parser.parse_args(["--send-digest-now"])
    assert args.send_digest_now is True


def test_cli_flags_can_be_combined() -> None:
    """``--once`` и ``--send-digest-now`` совместимы (cron-кейс)."""
    host = _ParserHost()
    args = host.parser.parse_args(["--once", "--send-digest-now"])
    assert args.once is True
    assert args.send_digest_now is True


# ─── /stats ───────────────────────────────────────────────────────────


def test_stats_command_shows_grouped_counts(
    storage: sqlite3.Connection,
) -> None:
    """``/stats`` возвращает отформатированный счётчик по профилям."""
    facade = StorageFacade(storage)
    _save_profile(facade, "p1", "Python Backend")
    _save_profile(facade, "p2", "Data Engineer")
    _save_draft(facade, profile_id="p1", vacancy_id=1, has_test=True)
    _save_draft(facade, profile_id="p1", vacancy_id=2, has_test=False)
    _save_draft(facade, profile_id="p2", vacancy_id=3, has_test=False)

    op = _build_op_with_real_digest(storage)
    transport = _make_transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    tool = _build_tool(storage)

    op._handle_update(  # type: ignore[attr-defined]
        _make_update("/stats"),
        transport,
        tool,
    )

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]  # type: ignore[attr-defined]
    assert "Черновики к ревью: 3" in text
    assert "Python Backend: 2" in text
    assert "Data Engineer: 1" in text
    assert "с тестами: 1" in text
    assert "без: 1" in text


def test_stats_command_when_no_drafts(storage: sqlite3.Connection) -> None:
    """Пустая БД → короткое сообщение «нет черновиков»."""
    op = _build_op_with_real_digest(storage)
    transport = _make_transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    tool = _build_tool(storage)

    op._handle_update(  # type: ignore[attr-defined]
        _make_update("/stats"),
        transport,
        tool,
    )

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]  # type: ignore[attr-defined]
    assert "Нет подготовленных черновиков" in text


def test_stats_command_handles_digest_service_failure(
    storage: sqlite3.Connection,
) -> None:
    """Падение ``collect_groups`` не валит бот — отвечаем «❌ …»."""
    op = Operation()
    digest = MagicMock()
    digest.collect_groups.side_effect = RuntimeError("DB is on fire")
    op._digest_service = digest  # type: ignore[attr-defined]

    transport = _make_transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    tool = _build_tool(storage)

    op._handle_update(  # type: ignore[attr-defined]
        _make_update("/stats"),
        transport,
        tool,
    )

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]  # type: ignore[attr-defined]
    assert "Не удалось получить статистику черновиков" in text


def test_stats_command_without_digest_service(
    storage: sqlite3.Connection,
) -> None:
    """Без инжектированного ``_digest_service`` сообщаем об ошибке."""
    op = Operation()  # _digest_service == None
    transport = _make_transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    tool = _build_tool(storage)

    op._handle_update(  # type: ignore[attr-defined]
        _make_update("/stats"),
        transport,
        tool,
    )

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]  # type: ignore[attr-defined]
    assert "Сервис дайджеста не инициализирован" in text


# ─── /review и /cancel (плейсхолдеры) ───────────────────────────────


def test_review_command_returns_placeholder(
    storage: sqlite3.Connection,
) -> None:
    """``/review`` отвечает заглушкой (полный flow — issue #9)."""
    op = _build_op_with_real_digest(storage)
    transport = _make_transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    tool = _build_tool(storage)

    op._handle_update(  # type: ignore[attr-defined]
        _make_update("/review"),
        transport,
        tool,
    )

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]  # type: ignore[attr-defined]
    assert "Review-флоу появится позже" in text
    assert "issue #9" in text


def test_cancel_command_returns_placeholder(
    storage: sqlite3.Connection,
) -> None:
    """``/cancel`` отвечает той же заглушкой (отмена появится в issue #9)."""
    op = _build_op_with_real_digest(storage)
    transport = _make_transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    tool = _build_tool(storage)

    op._handle_update(  # type: ignore[attr-defined]
        _make_update("/cancel"),
        transport,
        tool,
    )

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]  # type: ignore[attr-defined]
    assert "Review-флоу появится позже" in text
    assert "issue #9" in text


def test_help_lists_new_commands(storage: sqlite3.Connection) -> None:
    """``/help`` упоминает ``/stats``, ``/review``, ``/cancel``."""
    op = _build_op_with_real_digest(storage)
    transport = _make_transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    tool = _build_tool(storage)

    op._handle_update(  # type: ignore[attr-defined]
        _make_update("/help"),
        transport,
        tool,
    )

    transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]
    text = transport.send_message.call_args[0][1]  # type: ignore[attr-defined]
    assert "/stats" in text
    assert "/review" in text
    assert "/cancel" in text


# ─── Режим --once: один цикл и выход ────────────────────────────────


def test_once_mode_exits_after_one_cycle(
    storage: sqlite3.Connection,
    mock_telegram_transport: list[MagicMock],
) -> None:
    """``--once`` обрабатывает один batch и возвращает 0; ``get_updates``
    дёргается ровно один раз."""
    op = Operation()
    op._digest_service = _make_digest_mock(sent=True, total_drafts=5)  # type: ignore[attr-defined]

    tool = _build_tool(storage)
    args = argparse.Namespace(
        once=True,
        send_digest_now=False,
        profile_id="default",
        config_dir=None,
        verbosity=0,
        api_delay=None,
        user_agent=None,
        proxy_url=None,
        openai_proxy_url=None,
        operation_run=None,
    )

    rc = op.run(tool, args)  # type: ignore[arg-type]

    assert rc == 0
    assert len(mock_telegram_transport) == 1
    transport = mock_telegram_transport[0]
    transport.get_updates.assert_called_once()  # type: ignore[unused-coroutine]


def test_once_mode_calls_digest_send(
    storage: sqlite3.Connection,
    mock_telegram_transport: list[MagicMock],
) -> None:
    """``--once`` запускает ровно одну проверку дайджеста."""
    op = Operation()
    digest = _make_digest_mock(sent=True, total_drafts=7)
    op._digest_service = digest  # type: ignore[attr-defined]

    tool = _build_tool(storage, digest_time="00:00")
    args = argparse.Namespace(
        once=True,
        send_digest_now=False,
        profile_id="default",
        config_dir=None,
        verbosity=0,
        api_delay=None,
        user_agent=None,
        proxy_url=None,
        openai_proxy_url=None,
        operation_run=None,
    )

    op.run(tool, args)  # type: ignore[arg-type]

    digest.send.assert_called_once()  # type: ignore[unused-coroutine]


def test_once_mode_without_send_digest_now_uses_force_false(
    storage: sqlite3.Connection,
    mock_telegram_transport: list[MagicMock],
) -> None:
    """Без ``--send-digest-now`` дайджест вызывается с ``force=False``."""
    op = Operation()
    digest = _make_digest_mock(sent=False, skipped_reason="already_sent")
    op._digest_service = digest  # type: ignore[attr-defined]

    tool = _build_tool(storage, digest_time="00:00")
    args = argparse.Namespace(
        once=True,
        send_digest_now=False,
        profile_id="default",
        config_dir=None,
        verbosity=0,
        api_delay=None,
        user_agent=None,
        proxy_url=None,
        openai_proxy_url=None,
        operation_run=None,
    )

    op.run(tool, args)  # type: ignore[arg-type]

    digest.send.assert_called_once_with(force=False)  # type: ignore[unused-coroutine]


# ─── --send-digest-now ──────────────────────────────────────────────


def test_send_digest_now_triggers_force_send(
    storage: sqlite3.Connection,
    mock_telegram_transport: list[MagicMock],
) -> None:
    """``--send-digest-now`` пробрасывает ``force=True`` в ``send()``."""
    op = Operation()
    digest = _make_digest_mock(sent=True, total_drafts=3)
    op._digest_service = digest  # type: ignore[attr-defined]

    tool = _build_tool(storage, digest_time="00:00")
    args = argparse.Namespace(
        once=True,
        send_digest_now=True,
        profile_id="default",
        config_dir=None,
        verbosity=0,
        api_delay=None,
        user_agent=None,
        proxy_url=None,
        openai_proxy_url=None,
        operation_run=None,
    )

    rc = op.run(tool, args)  # type: ignore[arg-type]
    assert rc == 0
    digest.send.assert_called_once_with(force=True)  # type: ignore[unused-coroutine]


# ─── Идемпотентность дайджеста ──────────────────────────────────────


def test_digest_not_sent_twice_same_day(
    storage: sqlite3.Connection,
) -> None:
    """``send(force=False)`` дважды → реально отправляем только один раз.

    Бот не должен обходить идемпотентность сервиса: даже если он зовёт
    ``send()`` на каждом цикле, мок сервиса возвращает ``already_sent``,
    и ``send_message`` транспорта не дёргается повторно.
    """
    op = Operation()

    # Мок сервиса, который симулирует same-day идемпотентность: на первый
    # вызов ``send()`` отдаёт ``sent=True``, на второй — ``already_sent``.
    digest = MagicMock()
    digest.send.side_effect = [
        DigestResult(
            sent=True,
            total_drafts=4,
            message="ok",
        ),
        DigestResult(
            sent=False,
            skipped_reason="already_sent",
            total_drafts=4,
            message="ok",
        ),
    ]
    op._digest_service = digest  # type: ignore[attr-defined]

    transport = _make_transport(updates=[])
    transport.send_message = Mock()  # type: ignore[method-assign]
    # ``_resolve_chat_id`` сервиса мы не вызываем — в боте ``send_message``
    # транспорта — это низкоуровневый вызов, который делает сам сервис.
    # Здесь мы тестируем именно бота: на каждый цикл он зовёт ``send()``,
    # и если сервис говорит ``sent=False``, бот не пытается слать сам.
    tool = _build_tool(storage)

    # Прогоняем две итерации «руками» через helper-метод.
    # Передаём фиксированное время после 10:00, чтобы дайджест точно сработал
    fixed_now = datetime(2024, 1, 1, 12, 0, 0)
    for _ in range(2):
        op._maybe_send_digest(  # type: ignore[attr-defined]
            tool_config=tool.config,
            force=False,
            now=fixed_now,
        )

    # Два вызова ``send()`` (на каждый цикл), оба с force=False.
    assert digest.send.call_count == 2
    digest.send.assert_called_with(force=False)

    # Бот НЕ слал сообщения напрямую — этим занимается сервис внутри.
    transport.send_message.assert_not_called()  # type: ignore[unused-coroutine]


def test_digest_force_send_can_override_idempotency(
    storage: sqlite3.Connection,
) -> None:
    """``force=True`` доходит до ``send()`` и не «съедается» ботом."""
    op = Operation()
    digest = _make_digest_mock(sent=True, total_drafts=2)
    op._digest_service = digest  # type: ignore[attr-defined]

    tool = _build_tool(storage)
    # Передаём фиксированное время после 10:00, чтобы дайджест точно сработал
    fixed_now = datetime(2024, 1, 1, 12, 0, 0)
    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=True,
        now=fixed_now,
    )

    digest.send.assert_called_once_with(force=True)  # type: ignore[unused-coroutine]


# ─── Time-of-day гейт ───────────────────────────────────────────────


def test_digest_not_sent_before_configured_time(
    storage: sqlite3.Connection,
) -> None:
    """До ``daily_digest_time`` ``send()`` не вызывается."""
    op = Operation()
    digest = _make_digest_mock()
    op._digest_service = digest  # type: ignore[attr-defined]

    tool = _build_tool(storage, digest_time="10:00")

    # Время 09:00 — раньше таргета.
    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=False,
        now=datetime(2026, 6, 9, 9, 0, 0),
    )
    digest.send.assert_not_called()  # type: ignore[unused-coroutine]


def test_digest_sent_at_or_after_configured_time(
    storage: sqlite3.Connection,
) -> None:
    """В ``daily_digest_time`` и позже ``send()`` зовётся."""
    op = Operation()
    digest = _make_digest_mock(sent=True, total_drafts=1)
    op._digest_service = digest  # type: ignore[attr-defined]

    tool = _build_tool(storage, digest_time="10:00")

    # Ровно 10:00 — должно сработать (``>=``).
    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=False,
        now=datetime(2026, 6, 9, 10, 0, 0),
    )
    digest.send.assert_called_once()  # type: ignore[unused-coroutine]

    digest.send.reset_mock()  # type: ignore[attr-defined]
    # 11:30 — точно после.
    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=False,
        now=datetime(2026, 6, 9, 11, 30, 0),
    )
    digest.send.assert_called_once()  # type: ignore[unused-coroutine]


def test_digest_skipped_without_telegram_config(
    storage: sqlite3.Connection,
) -> None:
    """Без секции ``telegram`` в конфиге дайджест не трогается."""
    op = Operation()
    digest = _make_digest_mock()
    op._digest_service = digest  # type: ignore[attr-defined]

    op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config={},
        force=False,
        now=datetime(2026, 6, 9, 12, 0, 0),
    )
    digest.send.assert_not_called()  # type: ignore[unused-coroutine]


def test_digest_send_failure_does_not_propagate(
    storage: sqlite3.Connection,
) -> None:
    """Исключение из ``send()`` логируется, но не валит polling-цикл."""
    op = Operation()
    digest = MagicMock()
    digest.send.side_effect = RuntimeError("telegram down")
    op._digest_service = digest  # type: ignore[attr-defined]

    tool = _build_tool(storage)
    # Должно проглотить исключение и вернуть ``None``.
    result = op._maybe_send_digest(  # type: ignore[attr-defined]
        tool_config=tool.config,
        force=False,
        now=datetime(2026, 6, 9, 12, 0, 0),
    )
    assert result is None


# ─── /stats / /review / /cancel: обратная совместимость ─────────────


def test_existing_commands_still_work(
    storage: sqlite3.Connection,
) -> None:
    """``/start``, ``/help``, ``/status`` работают без digest-сервиса."""
    op = Operation()  # _digest_service == None
    transport = _make_transport()
    transport.send_message = Mock()  # type: ignore[method-assign]
    tool = MagicMock()
    tool.storage.negotiations.count_total.return_value = 10
    tool.storage.skipped_vacancies.count_total.return_value = 5
    tool.storage.application_drafts.count_total.return_value = 3

    for cmd in ("/start", "/help", "/status"):
        transport.send_message.reset_mock()  # type: ignore[attr-defined]
        op._handle_update(  # type: ignore[attr-defined]
            _make_update(cmd),
            transport,
            tool,
        )
        transport.send_message.assert_called_once()  # type: ignore[unused-coroutine]


# ─── Бот без bot_token — корректный exit code ───────────────────────


def test_run_returns_1_without_bot_token(
    storage: sqlite3.Connection,
    mock_telegram_transport: list[MagicMock],
) -> None:
    """Без ``telegram.bot_token`` бот сразу выходит с кодом 1."""
    op = Operation()
    tool = _build_tool(storage, bot_token="")
    args = argparse.Namespace(
        once=False,
        send_digest_now=False,
        profile_id="default",
        config_dir=None,
        verbosity=0,
        api_delay=None,
        user_agent=None,
        proxy_url=None,
        openai_proxy_url=None,
        operation_run=None,
    )

    assert op.run(tool, args) == 1  # type: ignore[arg-type]
    # Бот не должен был даже пытаться создать транспорт.
    assert mock_telegram_transport == []
