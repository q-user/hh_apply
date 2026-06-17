"""Pytest configuration for benchmarks.

Configures pytest-benchmark with appropriate settings for measuring
performance of async and sync operations.
"""

import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Add scripts directory for any standalone launchers
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def storage() -> Iterator[sqlite3.Connection]:
    """Fresh in-memory SQLite with initialized schema."""
    from hh_applicant_tool.storage import StorageFacade

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    StorageFacade(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def benchmark_storage(storage: sqlite3.Connection):
    """Alias for storage fixture for clarity in benchmarks."""
    return storage


# pytest-benchmark configuration
def pytest_configure(config):
    """Configure pytest-benchmark."""
    # Disable benchmark saving by default, only save when explicitly requested
    config.option.benchmark_save = getattr(
        config.option, "benchmark_save", None
    )
    config.option.benchmark_compare = getattr(
        config.option, "benchmark_compare", None
    )
    config.option.benchmark_compare_fail = getattr(
        config.option, "benchmark_compare_fail", "mean:10%"
    )


# Custom benchmark group markers
def pytest_benchmark_group_stats(group_name: str, stats: dict) -> None:
    """Custom hook for benchmark group statistics."""
    pass


# Benchmark fixtures for common test data
@pytest.fixture
def sample_vacancy_data():
    """Sample vacancy data for benchmarking."""
    return {
        "id": "12345678",
        "name": "Senior Python Developer",
        "area": {"id": "1", "name": "Москва"},
        "salary": {
            "from": 200000,
            "to": 300000,
            "currency": "RUR",
            "gross": True,
        },
        "employer": {"id": "12345", "name": "Tech Company", "trusted": True},
        "experience": {"id": "between3And6", "name": "От 3 до 6 лет"},
        "employment": {"id": "full", "name": "Полная занятость"},
        "schedule": {"id": "fullDay", "name": "Полный день"},
        "description": "Мы ищем опытного Python разработчика...",
        "key_skills": [
            {"name": "Python"},
            {"name": "FastAPI"},
            {"name": "PostgreSQL"},
        ],
        "published_at": "2024-01-15T10:00:00+0300",
        "created_at": "2024-01-15T10:00:00+0300",
        "has_test": False,
        "response_letter_required": False,
        "type": {"id": "open", "name": "Открытая"},
        "address": None,
        "alternate_url": "https://hh.ru/vacancy/12345678",
        "apply_alternate_url": "https://hh.ru/applicant/vacancy_response?vacancy_id=12345678",
        "insider_interview": None,
    }


@pytest.fixture
def sample_resume_data():
    """Sample resume data for benchmarking."""
    return {
        "id": "resume_123",
        "title": "Senior Python Developer",
        "first_name": "Ivan",
        "last_name": "Ivanov",
        "age": 30,
        "gender": {"id": "male", "name": "Мужской"},
        "area": {"id": "1", "name": "Москва"},
        "skills": "Python, FastAPI, PostgreSQL, Docker, Kubernetes, Redis, SQLAlchemy",
        "experience": [
            {
                "id": "exp_1",
                "company": "Tech Corp",
                "position": "Senior Developer",
                "start": "2020-01",
                "end": "2024-01",
                "description": "Разработка высоконагруженных систем на Python",
            }
        ],
        "education": {
            "primary": [
                {
                    "id": "edu_1",
                    "name": "МГУ",
                    "organization": "Факультет ВМК",
                    "year": 2015,
                }
            ]
        },
        "language": [
            {
                "id": "en",
                "name": "English",
                "level": {"id": "b2", "name": "Upper Intermediate"},
            }
        ],
        "salary": {"amount": 250000, "currency": "RUR"},
        "updated_at": "2024-01-15T10:00:00+0300",
    }


@pytest.fixture
def sample_employer_data():
    """Sample employer data for benchmarking."""
    return {
        "id": "12345",
        "name": "Tech Company",
        "alternate_url": "https://hh.ru/employer/12345",
        "logo_urls": {
            "original": "https://img.hh.ru/employer-logo/12345.png",
            "90": "https://img.hh.ru/employer-logo/12345_90.png",
            "240": "https://img.hh.ru/employer-logo/12345_240.png",
        },
        "trusted": True,
        "description": "We are a tech company...",
        "site_url": "https://techcompany.com",
        "industries": [{"id": "7", "name": "IT"}],
        "vacancies_url": "https://hh.ru/employer/12345/vacancies",
    }
