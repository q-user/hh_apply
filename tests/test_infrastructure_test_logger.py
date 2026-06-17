"""Тесты файлового логгера вакансий с тестами."""

from __future__ import annotations

import threading
from pathlib import Path

from job_bot.application_submit.services.test_logger import FileTestVacancyLogger

# ─── Базовый путь: log + read_logs ──────────────────────────────


def test_log_writes_line_to_file(tmp_path: Path):
    """log() дописывает строку в файл."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    logger = FileTestVacancyLogger(log_file)
    logger.log("Backend", "Acme", "https://hh.ru/vacancy/1")
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Backend" in content
    assert "Acme" in content
    assert "https://hh.ru/vacancy/1" in content


def test_log_includes_timestamp(tmp_path: Path):
    """log() вставляет timestamp в формате YYYY-MM-DD HH:MM:SS."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    logger = FileTestVacancyLogger(log_file)
    logger.log("Vacancy", "Employer", "https://hh.ru/vacancy/1")
    content = log_file.read_text(encoding="utf-8")
    # Дата вида [2026-06-09 12:34:56]
    import re

    assert re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", content)


def test_log_appends_multiple_lines(tmp_path: Path):
    """Несколько вызовов log() → строки накапливаются."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    logger = FileTestVacancyLogger(log_file)
    logger.log("A", "X", "url1")
    logger.log("B", "Y", "url2")
    logger.log("C", "Z", "url3")
    content = log_file.read_text(encoding="utf-8")
    assert content.count(" - ") >= 3
    for marker in ("A - X - url1", "B - Y - url2", "C - Z - url3"):
        assert marker in content


# ─── read_logs ──────────────────────────────────────────────────


def test_read_logs_returns_most_recent_first(tmp_path: Path):
    """read_logs() возвращает строки в обратном порядке (свежие первые)."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    logger = FileTestVacancyLogger(log_file)
    logger.log("A", "X", "url1")
    logger.log("B", "Y", "url2")
    logger.log("C", "Z", "url3")

    logs = logger.read_logs()
    # В обратном порядке: C, B, A
    assert "C" in logs[0]
    assert "B" in logs[1]
    assert "A" in logs[2]


def test_read_logs_with_limit(tmp_path: Path):
    """limit ограничивает количество возвращаемых строк."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    logger = FileTestVacancyLogger(log_file)
    for i in range(5):
        logger.log(f"V{i}", f"E{i}", f"url{i}")
    logs = logger.read_logs(limit=2)
    assert len(logs) == 2
    # Самые свежие — V4, V3
    assert "V4" in logs[0]
    assert "V3" in logs[1]


def test_read_logs_strips_newline(tmp_path: Path):
    """read_logs() возвращает строки без \n на конце."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    logger = FileTestVacancyLogger(log_file)
    logger.log("V", "E", "url")
    logs = logger.read_logs()
    assert logs[0][-1] != "\n"


def test_read_logs_on_missing_file_returns_empty(tmp_path: Path):
    """Файл ещё не создан — read_logs() возвращает []."""
    log_file = tmp_path / "missing.txt"
    logger = FileTestVacancyLogger(log_file)
    assert logger.read_logs() == []


# ─── clear ──────────────────────────────────────────────────────


def test_clear_removes_file(tmp_path: Path):
    """clear() удаляет файл."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    logger = FileTestVacancyLogger(log_file)
    logger.log("V", "E", "url")
    assert log_file.exists()
    logger.clear()
    assert not log_file.exists()


def test_clear_on_missing_file_is_noop(tmp_path: Path):
    """clear() на отсутствующем файле — без ошибок."""
    log_file = tmp_path / "missing.txt"
    logger = FileTestVacancyLogger(log_file)
    # Должно отработать тихо
    logger.clear()
    assert not log_file.exists()


# ─── Ротация ────────────────────────────────────────────────────


def test_rotation_when_file_exceeds_max_size(tmp_path: Path):
    """При превышении max_file_size файл ротируется в .1."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    # Минимальный размер 100 байт, чтобы триггерить ротацию
    logger = FileTestVacancyLogger(
        log_file,
        max_file_size=100,
        max_files=3,
    )

    # Длинная строка, чтобы переполнить 100 байт
    long_line = "X" * 200
    logger.log("V1", "E1", long_line)
    # Следующий log() триггернёт ротацию
    logger.log("V2", "E2", long_line)

    # Ротация заменяет суффикс .txt на .1 (with_suffix('.1'))
    rotated = log_file.with_suffix(".1")
    assert rotated.exists()
    # В текущем файле остался только последний лог
    content = log_file.read_text(encoding="utf-8")
    assert "V2" in content
    assert "V1" not in content


def test_rotation_keeps_max_files(tmp_path: Path):
    """Количество rotated файлов не превышает max_files."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    logger = FileTestVacancyLogger(
        log_file,
        max_file_size=50,
        max_files=2,
    )
    long_line = "X" * 100
    # Делаем 4 записи → должно быть 2 ротации (.1 и .2)
    for i in range(4):
        logger.log(f"V{i}", f"E{i}", long_line)

    # .1 и .2 должны существовать, .3 — нет
    assert log_file.with_suffix(".1").exists()
    assert log_file.with_suffix(".2").exists()
    assert not log_file.with_suffix(".3").exists()


# ─── Родительская директория ────────────────────────────────────


def test_logger_creates_parent_directory(tmp_path: Path):
    """Если родительской директории нет — она создаётся."""
    nested = tmp_path / "subdir" / "logs" / "vacancies.txt"
    logger = FileTestVacancyLogger(nested)
    logger.log("V", "E", "url")
    assert nested.exists()
    assert "V" in nested.read_text(encoding="utf-8")


# ─── Thread-safety smoke test ───────────────────────────────────


def test_logger_is_thread_safe(tmp_path: Path):
    """Параллельные log() из разных потоков не теряют записи."""
    log_file = tmp_path / "vacancies_with_tests.txt"
    logger = FileTestVacancyLogger(log_file)

    def worker(prefix: str) -> None:
        for i in range(10):
            logger.log(f"{prefix}-{i}", "E", "url")

    threads = [
        threading.Thread(target=worker, args=(f"T{i}",)) for i in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    content = log_file.read_text(encoding="utf-8")
    # 5 потоков * 10 строк = 50 записей
    lines = [line for line in content.splitlines() if line.strip()]
    assert len(lines) == 50
