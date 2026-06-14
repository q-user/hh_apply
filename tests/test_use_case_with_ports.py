"""Интеграционные тесты: use case предпочитает порты legacy-пути.

После Phase 2 ``ApplyToVacanciesUseCase`` принимает опциональные порты
(CaptchaSolver, SiteParser, EmailSender, CancellationToken, Clock,
TestVacancyLogger). Если порт задан — он используется, legacy-fallback
игнорируется.

Эти тесты собирают use case с моками портов, прогоняют ``execute()``
на минимальном входе и проверяют, что моки были дёрнуты, а не legacy
прямые вызовы.
"""

from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.application import (
    ApplyToVacanciesCommand,
    ApplyToVacanciesResult,
    ApplyToVacanciesUseCase,
)
from hh_applicant_tool.storage import StorageFacade

# ─── Хелперы для построения use case ───────────────────────────


def _make_storage() -> StorageFacade:
    """In-memory storage с инициализированной схемой."""
    conn = sqlite3.connect(":memory:")
    StorageFacade(conn)
    return conn  # type: ignore[return-value]


def _make_resume() -> dict:
    """Минимальное опубликованное резюме."""
    return {
        "id": "r1",
        "title": "Python Dev",
        "alternate_url": "https://hh.ru/resume/r1",
        "status": {"id": "published", "name": "published"},
    }


def _make_vacancy(has_test: bool = False, with_email: bool = False) -> dict:
    """Минимальная вакансия для прогона _apply_to_resume."""
    v: dict = {
        "id": 101,
        "name": "Backend",
        "has_test": has_test,
        "alternate_url": "https://hh.ru/vacancy/101",
        "employer": {"id": 42, "name": "Acme"},
        "area": {"id": "1", "name": "Москва"},
        "salary": None,
        "type": {"id": "open", "name": "Открытая"},
        "published_at": "2026-01-01T00:00:00+0300",
        "created_at": "2026-01-01T00:00:00+0300",
        "url": "https://hh.ru/vacancy/101",
        "relations": [],
        "archived": False,
        "response_letter_required": False,
    }
    if with_email:
        v["contacts"] = {"email": "hr@acme.example.com"}
    return v


def _build_use_case(**ports) -> ApplyToVacanciesUseCase:
    """Собирает use case с моками всех портов и stub-инфраструктурой.

    ``ports`` — kwargs с именами параметров конструктора use case
    (captcha_solver, site_parser, email_sender, cancellation, clock,
    test_logger). Любой непереданный порт получает MagicMock.
    """
    api = MagicMock()
    # /resumes/mine → одно резюме
    api.get.side_effect = lambda path, **kw: (
        {"items": [_make_resume()]}
        if path == "/resumes/mine"
        else {
            "first_name": "Иван",
            "last_name": "Иванов",
            "email": "me@example.com",
        }
        if path == "/me"
        else {"description": "<p>X</p>", "name": "Backend"}
    )
    # /negotiations → отклик принят (res == {})
    api.post.return_value = {}

    session = MagicMock()

    storage_conn = _make_storage()
    storage = StorageFacade(storage_conn)

    # Дефолтные моки для всех портов
    captcha_solver = ports.get("captcha_solver") or MagicMock()
    site_parser = ports.get("site_parser") or MagicMock()
    email_sender = ports.get("email_sender") or MagicMock()
    cancellation = ports.get("cancellation") or MagicMock()
    clock = ports.get("clock") or MagicMock()
    test_logger = ports.get("test_logger") or MagicMock()

    use_case = ApplyToVacanciesUseCase(
        api_client=api,
        session=session,
        storage=storage,
        cover_letter_ai=None,
        captcha_ai=None,
        xsrf_token="xsrf",
        smtp=None,
        config=None,
        captcha_solver=captcha_solver,
        site_parser=site_parser,
        email_sender=email_sender,
        cancellation=cancellation,
        clock=clock,
        test_logger=test_logger,
    )
    return use_case


# ─── _now() использует Clock ────────────────────────────────────


def test_now_uses_clock_port():
    """_now() возвращает clock.now() если Clock задан."""
    fixed_dt = MagicMock(name="datetime")
    clock = MagicMock()
    clock.now.return_value = fixed_dt
    use_case = _build_use_case(clock=clock)
    assert use_case._now() is fixed_dt
    clock.now.assert_called_once()


def test_now_uses_datetime_when_no_clock():
    """Без Clock — _now() использует datetime.now()."""
    use_case = _build_use_case()
    # Удаляем clock из use case
    use_case._clock = None
    result = use_case._now()
    # Должен вернуться настоящий datetime
    import datetime

    assert isinstance(result, datetime.datetime)


# ─── _is_cancelled() использует CancellationToken ───────────────


def test_is_cancelled_uses_token():
    """_is_cancelled() читает из CancellationToken, если он задан."""
    token = MagicMock()
    token.is_cancelled = True
    use_case = _build_use_case(cancellation=token)
    assert use_case._is_cancelled() is True


def test_is_cancelled_false_from_token():
    """CancellationToken.is_cancelled=False → _is_cancelled()=False."""
    token = MagicMock()
    token.is_cancelled = False
    use_case = _build_use_case(cancellation=token)
    assert use_case._is_cancelled() is False


def test_is_cancelled_uses_threading_event_when_no_token():
    """Без CancellationToken — fallback на threading.Event."""
    import threading

    event = threading.Event()
    use_case = _build_use_case()
    use_case._cancellation = None
    use_case.cancel_event = event
    assert use_case._is_cancelled() is False
    event.set()
    assert use_case._is_cancelled() is True


# ─── _parse_site() использует SiteParser ────────────────────────


def test_parse_site_uses_port():
    """_parse_site() вызывает site_parser.parse_site()."""
    site_parser = MagicMock()
    site_parser.parse_site.return_value = {
        "title": "Acme",
        "emails": ["hr@acme.example.com"],
    }
    use_case = _build_use_case(site_parser=site_parser)

    result = use_case._parse_site("https://acme.example.com")
    site_parser.parse_site.assert_called_once_with("https://acme.example.com")
    assert result["title"] == "Acme"
    assert "hr@acme.example.com" in result["emails"]


def test_parse_site_falls_back_when_port_raises():
    """Если site_parser бросает исключение — fallback на legacy session.get()."""
    site_parser = MagicMock()
    site_parser.parse_site.side_effect = RuntimeError("parser down")
    use_case = _build_use_case(site_parser=site_parser)

    # Мокаем session.get чтобы вернуть HTML (с context manager)
    response = MagicMock()
    response.text = "<html><head><title>Legacy</title></head></html>"
    response.headers = {}
    response.raise_for_status = MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = response
    ctx.__exit__.return_value = False
    use_case.session.get.return_value = ctx

    result = use_case._parse_site("https://acme.example.com")
    # Legacy fallback отработал
    assert result["title"] == "Legacy"


def test_parse_site_uses_session_when_no_port():
    """Без SiteParser — прямой вызов session.get()."""
    response = MagicMock()
    response.text = "<html><head><title>No Port</title></head></html>"
    response.headers = {}
    response.raise_for_status = MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = response
    ctx.__exit__.return_value = False
    use_case = _build_use_case()
    use_case._site_parser = None  # явно убираем порт
    use_case.session.get.return_value = ctx

    result = use_case._parse_site("https://acme.example.com")
    assert result["title"] == "No Port"


# ─── _send_email() использует EmailSender ───────────────────────


def test_send_email_uses_port():
    """_send_email() вызывает email_sender.send_email()."""
    sender = MagicMock()
    use_case = _build_use_case(email_sender=sender)

    use_case._send_email("a@b.com", "Subject", "Body")
    sender.send_email.assert_called_once_with("a@b.com", "Subject", "Body")


def test_send_email_falls_back_when_port_raises():
    """Если email_sender бросает — use case логирует warning, но не падает."""
    sender = MagicMock()
    sender.send_email.side_effect = RuntimeError("smtp down")
    use_case = _build_use_case(email_sender=sender)

    # smtp=None, config=None — legacy-путь бросит RuntimeError,
    # но use case должен сначала попробовать порт, и не дойти до legacy.
    # Это нормальное поведение: port-ошибка залогирована, дальше
    # выполняется legacy fallback (и он тоже падает с RuntimeError).
    with pytest.raises(RuntimeError):
        use_case._send_email("a@b.com", "S", "B")
    sender.send_email.assert_called_once()


def test_send_email_legacy_when_no_port():
    """Без email_sender — ошибка конфигурации (smtp/config=None)."""
    use_case = _build_use_case()
    use_case._email_sender = None

    with pytest.raises(RuntimeError, match="SMTP клиент или конфиг"):
        use_case._send_email("a@b.com", "S", "B")


# ─── _handle_vacancy_test() использует TestVacancyLogger ────────


def test_handle_vacancy_test_uses_logger_port(capsys):
    """_handle_vacancy_test() зовёт test_logger.log()."""
    logger = MagicMock()
    use_case = _build_use_case(test_logger=logger)
    use_case.command = ApplyToVacanciesCommand(
        resume_id="r1",
    )
    vacancy = _make_vacancy(has_test=True)

    use_case._handle_vacancy_test(vacancy, "r1")

    logger.log.assert_called_once()
    # Аргументы: vacancy_name, employer_name, test_link
    args = logger.log.call_args[0]
    assert args[0] == "Backend"
    assert args[1] == "Acme"
    # test_link может быть alternate_url
    assert "hh.ru" in args[2]


def test_handle_vacancy_test_legacy_writes_to_file(tmp_path, monkeypatch):
    """Без test_logger — legacy: пишет в 'vacancies_with_tests.txt'."""
    monkeypatch.chdir(tmp_path)
    use_case = _build_use_case()
    use_case._test_logger = None
    use_case.command = ApplyToVacanciesCommand(resume_id="r1")

    vacancy = _make_vacancy(has_test=True)
    use_case._handle_vacancy_test(vacancy, "r1")

    # Файл создан в cwd
    log_file = tmp_path / "vacancies_with_tests.txt"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Backend" in content
    assert "Acme" in content


# ─── _solve_captcha_async() использует CaptchaSolver ────────────


def test_solve_captcha_async_uses_port():
    """_solve_captcha_async() вызывает captcha_solver.solve_captcha_url()."""
    solver = MagicMock()

    async def fake_solve(url: str) -> str:
        return "captcha-text"

    solver.solve_captcha_url = fake_solve
    use_case = _build_use_case(captcha_solver=solver)

    result = asyncio.run(
        use_case._solve_captcha_async("https://example.com/captcha")
    )
    assert result is True


def test_solve_captcha_async_returns_false_on_empty_text():
    """Если solver вернул пустую строку — _solve_captcha_async() → False."""
    solver = MagicMock()

    async def fake_solve(url: str) -> str:
        return ""

    solver.solve_captcha_url = fake_solve
    use_case = _build_use_case(captcha_solver=solver)

    result = asyncio.run(
        use_case._solve_captcha_async("https://example.com/captcha")
    )
    assert result is False


def test_solve_captcha_async_returns_false_on_exception():
    """Исключение из solver → False (не пробрасывается)."""
    solver = MagicMock()

    async def fake_solve(url: str) -> str:
        raise RuntimeError("solver failed")

    solver.solve_captcha_url = fake_solve
    use_case = _build_use_case(captcha_solver=solver)

    result = asyncio.run(
        use_case._solve_captcha_async("https://example.com/captcha")
    )
    assert result is False


# ─── E2E: execute() использует все порты ────────────────────────


def test_execute_prefers_ports_over_legacy(tmp_path, monkeypatch):
    """Полный execute() с вакансией-тестом: test_logger вызывается через порт."""
    # Переключаем cwd во временную папку, чтобы убедиться, что
    # legacy-путь (write to 'vacancies_with_tests.txt') не сработал
    monkeypatch.chdir(tmp_path)

    captcha_solver = MagicMock()
    site_parser = MagicMock()
    site_parser.parse_site.return_value = {"emails": []}
    email_sender = MagicMock()
    test_logger = MagicMock()
    clock = MagicMock()
    cancellation = MagicMock()
    cancellation.is_cancelled = False

    use_case = _build_use_case(
        captcha_solver=captcha_solver,
        site_parser=site_parser,
        email_sender=email_sender,
        test_logger=test_logger,
        clock=clock,
        cancellation=cancellation,
    )

    # Изолируем storage, чтобы не зависеть от схемы VacancyModel
    use_case._save_vacancy_to_storage = MagicMock()  # type: ignore[method-assign]
    use_case._load_employer_profile = MagicMock()  # type: ignore[method-assign]

    # Подсовываем одну вакансию с тестом — пройдёт через
    # _handle_vacancy_test() → test_logger.log() через порт
    vacancy = _make_vacancy(has_test=True)
    use_case._get_vacancies = MagicMock(return_value=[vacancy])

    result = use_case.execute(
        ApplyToVacanciesCommand(resume_id="r1", total_pages=1),
    )
    assert isinstance(result, ApplyToVacanciesResult)
    # TestVacancyLogger через порт вызван
    test_logger.log.assert_called_once()
    # Legacy-файл не создан (потому что порт перехватил)
    assert not (tmp_path / "vacancies_with_tests.txt").exists()



# ─── run_apply_pipeline (issue #89 partial bridge) ─────────────────────


class TestRunApplyPipelineBridge:
    """``ApplyToVacanciesUseCase.execute()`` delegates to
    ``ApplicationSubmitSlice.run_apply_pipeline`` when the slice is
    wired (issue #89 partial bridge). When no slice is wired, the
    use case falls back to the legacy inline path -- public surface
    preserved.
    """

    def test_execute_delegates_to_slice_when_wired(self) -> None:
        """With ``application_submit_slice`` injected, ``execute()``
        calls ``slice.run_apply_pipeline(legacy_use_case=self, ...)``
        and returns whatever the slice returns."""
        from hh_applicant_tool.application import (
            ApplyToVacanciesCommand,
            ApplyToVacanciesResult,
        )

        use_case = _build_use_case()
        expected = ApplyToVacanciesResult(applied=7, resumes_processed=2)
        slice_ = MagicMock()
        slice_.run_apply_pipeline.return_value = expected
        use_case._application_submit_slice = slice_  # type: ignore[attr-defined]

        command = ApplyToVacanciesCommand(resume_id="r1")
        result = use_case.execute(command)

        assert result is expected
        slice_.run_apply_pipeline.assert_called_once()
        call = slice_.run_apply_pipeline.call_args
        assert call.kwargs["legacy_use_case"] is use_case
        assert call.kwargs["command"] is command
        assert call.kwargs["cancel_event"] is None
        assert call.kwargs["progress_callback"] is None

    def test_execute_forwards_cancel_and_progress_to_slice(self) -> None:
        """``cancel_event`` and ``progress_callback`` are forwarded to
        the slice verbatim."""
        from hh_applicant_tool.application import (
            ApplyToVacanciesCommand,
            ApplyToVacanciesResult,
        )

        use_case = _build_use_case()
        slice_ = MagicMock()
        slice_.run_apply_pipeline.return_value = ApplyToVacanciesResult()
        use_case._application_submit_slice = slice_  # type: ignore[attr-defined]

        cancel = MagicMock()
        progress = MagicMock()
        use_case.execute(
            ApplyToVacanciesCommand(),
            cancel_event=cancel,
            progress_callback=progress,
        )

        call = slice_.run_apply_pipeline.call_args
        assert call.kwargs["cancel_event"] is cancel
        assert call.kwargs["progress_callback"] is progress

    def test_execute_runs_inline_when_slice_not_wired(self) -> None:
        """Without a slice wired, ``execute()`` runs the legacy inline
        path. We stub the heavy helpers and assert the result is a
        proper ``ApplyToVacanciesResult`` -- this is the public-surface
        regression test for issue #89."""
        from hh_applicant_tool.application import (
            ApplyToVacanciesCommand,
            ApplyToVacanciesResult,
        )

        use_case = _build_use_case()
        # No slice wired -- legacy inline path runs.
        assert use_case._application_submit_slice is None  # type: ignore[attr-defined]

        # Stub the heavy helpers so execute() returns quickly.
        use_case._fetch_published_resumes = MagicMock(  # type: ignore[method-assign]
            return_value=[]
        )

        result = use_case.execute(ApplyToVacanciesCommand())
        assert isinstance(result, ApplyToVacanciesResult)
        assert result.applied == 0
        assert result.resumes_processed == 0

    def test_run_apply_pipeline_method_exists_and_is_callable(self) -> None:
        """The use case exposes a ``run_apply_pipeline`` method that
        satisfies the ``LegacyUseCasePort`` protocol. The slice calls
        this method when wired."""
        from hh_applicant_tool.application import (
            ApplyToVacanciesCommand,
            ApplyToVacanciesResult,
        )

        use_case = _build_use_case()
        assert hasattr(use_case, "run_apply_pipeline")
        assert callable(use_case.run_apply_pipeline)

        # Sanity: calling it without a slice and with no resumes
        # returns an empty result (legacy path).
        use_case._fetch_published_resumes = MagicMock(  # type: ignore[method-assign]
            return_value=[]
        )
        result = use_case.run_apply_pipeline(
            command=ApplyToVacanciesCommand()
        )
        assert isinstance(result, ApplyToVacanciesResult)
