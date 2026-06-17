"""Tests for :class:`ResumeRenderer` (issue #151).

The VSA port of the legacy :mod:`hh_applicant_tool.utils.resume_md`
module exposes a :class:`ResumeRenderer` service that turns a
markdown resume template into the dict payload expected by
``POST /resumes``. The renderer preserves the legacy behaviour bit-
for-bit: same heading parsing, same RU->API mapping, same
``_suggest`` placeholder scheme.

Tests use a small but representative markdown sample and assert the
structure of the produced dict. The dict is recursive and contains
nested RU->API translations, so tests focus on the observable
contract rather than every field.
"""

from __future__ import annotations

import pytest

from job_bot.resume_management.services.resume_renderer import ResumeRenderer

# A compact but representative resume template. Covers:
#  * personal data (RU->API key translation + gender mapping)
#  * desired title (first line of section)
#  * contacts (email + phone + comment)
#  * salary (RU currency -> ISO code)
#  * city (returned as a ``_suggest`` placeholder)
#  * professional roles (returned as ``_suggest`` placeholders)
#  * employment + schedule (RU->API translation)
#  * relocation (type + cities as ``_suggest``)
#  * business trip + travel time (RU->API translation)
#  * citizenship (returned as ``_suggest``)
#  * work ticket (returned as ``_suggest``)
#  * driver license + vehicle
#  * languages (RU name -> ISO code, RU level -> API level)
#  * skills (list of values)
#  * experience (period -> start/end dates, nested industries/sites)
#  * education (level + primary entries)
#  * recommendations
#  * sites/profiles (label->API type, URL passthrough)
SAMPLE_RESUME_MD = """\
# Иванов Иван Иванович

## Личные данные
- Имя: Иван
- Фамилия: Иванов
- Отчество: Иванович
- Дата рождения: 15.03.1990
- Пол: мужской

## Желаемая должность
Senior Python Developer

## Контакты
- Email: ivan@example.com
- Мобильный: +7 916 123-45-67 (рабочий)

## Зарплата
300 000 руб.

## Место проживания
Москва

## Профессиональные роли
- Senior Python Developer
- Backend Developer

## Занятость
- полная занятость
- удалённая работа

## График работы
- полный день
- сменный график

## Переезд
- Тип: готов
- Города: Москва, Санкт-Петербург

## Командировки
готов

## Время в пути
не важно

## Гражданство
- Россия

## Право на работу
- Россия
- Беларусь

## Водительское удостоверение
- B
- Автомобиль: да

## Языки
- Английский: продвинутый
- Русский: родной

## Ключевые навыки
- Python
- FastAPI
- PostgreSQL

## О себе
Опытный разработчик с 10+ годами опыта.

## Опыт работы
### Acme Corp
- Должность: Senior Developer
- Город: Москва
- Начало: 01.2020
- Конец: по настоящее время
- Отрасль: Информационные технологии
- Сайт: https://acme.example
Большой опыт работы над высоконагруженными системами.

### Beta LLC
- Должность: Developer
- Город: Санкт-Петербург
- Период: 03.2018 — 12.2019
- Отрасль: Финансы
- Сайт: https://beta.example
Работал над банковскими системами.

## Образование
- Уровень: высшее
### МГУ
- Факультет: ВМК
- Специальность: Прикладная математика
- Год окончания: 2014

## Рекомендации
### Петров Пётр
- Должность: CTO
- Организация: Acme Corp
- Контакт: petrov@example.com

## Сайты
- GitHub: https://github.com/ivan
- LinkedIn: https://linkedin.com/in/ivan
"""


@pytest.fixture
def renderer() -> ResumeRenderer:
    return ResumeRenderer()


def test_render_returns_dict(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    assert isinstance(payload, dict)


def test_render_personal_data_translated(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    assert payload["first_name"] == "Иван"
    assert payload["last_name"] == "Иванов"
    assert payload["middle_name"] == "Иванович"
    assert payload["birth_date"] == "1990-03-15"
    assert payload["gender"] == {"id": "male"}


def test_render_title(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    assert payload["title"] == "Senior Python Developer"


def test_render_contacts(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    contacts = payload["contact"]
    assert isinstance(contacts, list)
    # email + cell
    types = [c["type"]["id"] for c in contacts]
    assert "email" in types
    assert "cell" in types
    email = next(c for c in contacts if c["type"]["id"] == "email")
    assert email["value"] == "ivan@example.com"
    phone = next(c for c in contacts if c["type"]["id"] == "cell")
    assert phone["value"]["country"] == "7"
    assert phone["value"]["city"] == "916"
    assert phone["value"]["number"] == "1234567"
    assert phone.get("comment") == "рабочий"


def test_render_salary(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    assert payload["salary"] == {"amount": 300000, "currency": "RUR"}


def test_render_area_is_suggest_placeholder(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    assert payload["area"] == {
        "_suggest": "/suggests/area_leaves",
        "text": "Москва",
    }


def test_render_professional_roles_are_suggest_placeholders(
    renderer: ResumeRenderer,
) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    roles = payload["professional_roles"]
    assert all(r["_suggest"] == "/suggests/professional_roles" for r in roles)
    assert {r["text"] for r in roles} >= {
        "Senior Python Developer",
        "Backend Developer",
    }


def test_render_employments_and_schedules_translated(
    renderer: ResumeRenderer,
) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    # Employment: "полная занятость" -> "full".
    assert payload["employments"] == [{"id": "full"}]
    # Schedule: "полный день" -> "fullDay" and
    # "сменный график" -> "shift".
    assert payload["schedules"] == [{"id": "fullDay"}, {"id": "shift"}]


def test_render_relocation(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    reloc = payload["relocation"]
    assert reloc["type"] == {"id": "relocation_possible"}
    cities = reloc["area"]
    assert {c["text"] for c in cities} == {"Москва", "Санкт-Петербург"}
    assert all(c["_suggest"] == "/suggests/area_leaves" for c in cities)


def test_render_business_trip_and_travel_time(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    assert payload["business_trip_readiness"] == {"id": "ready"}
    assert payload["travel_time"] == {"id": "any"}


def test_render_citizenship_and_work_ticket(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    cit = payload["citizenship"]
    assert cit == [{"_suggest": "/suggests/areas", "text": "Россия"}]
    ticket = payload["work_ticket"]
    assert {c["text"] for c in ticket} == {"Россия", "Беларусь"}


def test_render_driver_license_and_vehicle(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    assert payload["driver_license_types"] == [{"id": "B"}]
    assert payload["has_vehicle"] is True


def test_render_languages(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    langs = payload["language"]
    by_id = {entry["id"]: entry["level"]["id"] for entry in langs}
    assert by_id["eng"] == "c1"
    assert by_id["rus"] == "l1"


def test_render_skills(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    assert payload["skill_set"] == ["Python", "FastAPI", "PostgreSQL"]
    assert "10+ годами" in payload["skills"]


def test_render_experience(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    exp = payload["experience"]
    assert len(exp) == 2
    acme, beta = exp
    assert acme["company"] == "Acme Corp"
    assert acme["position"] == "Senior Developer"
    assert acme["start"] == "2020-01-01"
    assert "end" not in acme  # "по настоящее время" -> no end
    assert acme["industries"] == [{"name": "Информационные технологии"}]
    assert acme["company_url"] == "https://acme.example"
    assert "высоконагруженными" in acme["description"]

    assert beta["company"] == "Beta LLC"
    assert beta["start"] == "2018-03-01"
    assert beta["end"] == "2019-12-01"


def test_render_education(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    edu = payload["education"]
    assert edu["level"] == {"id": "higher"}
    primary = edu["primary"]
    assert primary[0]["name"] == "МГУ"
    assert primary[0]["organization"] == "ВМК"
    assert primary[0]["result"] == "Прикладная математика"
    assert primary[0]["year"] == 2014


def test_render_recommendations(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    recs = payload["recommendation"]
    assert len(recs) == 1
    assert recs[0]["name"] == "Петров Пётр"
    assert recs[0]["position"] == "CTO"
    assert recs[0]["organization"] == "Acme Corp"
    assert recs[0]["contact"] == "petrov@example.com"


def test_render_sites(renderer: ResumeRenderer) -> None:
    payload = renderer.render(SAMPLE_RESUME_MD)
    sites = payload["site"]
    by_type = {s["type"]["id"]: s["url"] for s in sites}
    assert by_type["github"] == "https://github.com/ivan"
    assert by_type["linkedin"] == "https://linkedin.com/in/ivan"


def test_render_empty_markdown_is_empty_dict(renderer: ResumeRenderer) -> None:
    """An empty markdown document produces an empty dict (no sections)."""
    assert renderer.render("") == {}


def test_render_iso_date_passthrough(renderer: ResumeRenderer) -> None:
    """The ``_parse_date`` helper accepts both ``MM.YYYY`` and ``YYYY-MM-DD``."""
    md = """\
## Опыт работы
### Foo
- Должность: Dev
- Начало: 2020-01-01
- Конец: 2021-12-31
Worked on stuff.
"""
    payload = renderer.render(md)
    assert payload["experience"][0]["start"] == "2020-01-01"
    assert payload["experience"][0]["end"] == "2021-12-31"


def test_render_parse_resume_md_alias_exists() -> None:
    """The legacy module-level ``parse_resume_md`` is still importable.

    Other VSA code (``resume_management/handlers/resume_create_handler.py``)
    imports the module-level helper for the FileSystemTemplateLoader.
    The class is the canonical VSA surface; the free function is
    preserved as a thin alias for backwards compatibility.
    """
    from job_bot.resume_management.services import resume_renderer

    assert callable(resume_renderer.parse_resume_md)
    assert resume_renderer.parse_resume_md("") == {}
