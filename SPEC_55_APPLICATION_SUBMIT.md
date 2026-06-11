# ТЗ: Issue #55 — Application Submit Switchover (VSA)

## Цель
Заменить использование legacy-сервисов (`apply_one.py`, `apply_worker.py`) на новый `ApplicationSubmitSlice` в `ApplyToVacanciesUseCase` и `apply-worker` операции, оставив legacy-код с `DeprecationWarning` для обратной совместимости.

---

## Текущее состояние

### Что уже сделано (частично)
- ✅ Создан `ApplicationSubmitSlice` в `src/job_bot/application_submit/slice.py`
- ✅ Порты: `ApplyOnePort`, `TestPort` в `src/job_bot/application_submit/ports/`
- ✅ Хэндлер: `ApplyOneHandler` в `src/job_bot/application_submit/handlers/apply_one_handler.py`
- ✅ Частично изменены файлы:
  - `container.py` — добавлен `_get_application_submit_slice()`, `create_application_submit_adapter()`, класс `_ApplicationSubmitAdapter`
  - `apply_to_vacancies.py` — добавлен параметр `application_submit_adapter`, логика использования в `_send_apply_request`
  - `apply_one.py` — добавлено предупреждение об устаревании

### Проблемы для исправления
1. **Ruff E402** в `apply_one.py` — импорт после `warnings.warn()`
2. **2 падающих теста**:
   - `tests/test_ui_api.py::TestErrorMessages::test_apply_vacancies_ignores_unknown_keys`
   - `tests/test_vsa_vacancy_search_wiring.py::TestVacancySearchSliceWiring::test_apply_to_vacancies_use_case_receives_factory`
3. Возможные проблемы в логике адаптера (нужно проверить интеграцию)

---

## Детальное ТЗ

### 1. Исправить `src/hh_applicant_tool/services/apply_one.py`

**Проблема:** Ruff E402 — все импорты должны быть в начале файла.

**Решение:**
```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any
import warnings
from ..storage.models.application_draft import ApplicationDraftModel

# Deprecation warning ПОСЛЕ всех импортов
warnings.warn(
    "hh_applicant_tool.services.apply_one is deprecated; "
    "use job_bot.application_submit.slice.ApplicationSubmitSlice.apply_one instead",
    DeprecationWarning,
    stacklevel=2,
)

if TYPE_CHECKING:
    from .apply_worker import ApplyOneDraftFn

# ... остальной код функции make_default_apply_one
```

### 2. Исправить `src/hh_applicant_tool/services/apply_worker.py`

Добавить аналогичное предупреждение об устаревании:

```python
from __future__ import annotations
import warnings

warnings.warn(
    "hh_applicant_tool.services.apply_worker is deprecated; "
    "use job_bot.application_submit.slice.ApplicationSubmitSlice.worker instead",
    DeprecationWarning,
    stacklevel=2,
)

# ... остальные импорты
```

### 3. Проверить и исправить `container.py`

**Что проверить:**
- `_get_application_submit_slice()` — корректно создаёт слайс?
- `create_application_submit_adapter()` — возвращает адаптер?
- `_ApplicationSubmitAdapter.apply_one()` — корректно создаёт `ApplicationDraftModel` и вызывает `slice.apply_one(draft)`?
- В `apply_to_vacancies_use_case()` — адаптер передаётся в use case?

**Потенциальные проблемы:**
- Слайс создаётся с `storage_conn=create_database(...)` — это создаёт НОВОЕ соединение, а не использует `tool.storage`? Нужно использовать существующее `tool.storage`.
- `xsrf_token` передаётся в слайс — корректно ли извлекается?
- Адаптер сохраняет draft через `self._storage.application_drafts.save(draft)` — метод `save` существует?

### 4. Проверить и исправить `apply_to_vacancies.py`

**Ключевые моменты:**
- `_send_apply_request` использует адаптер когда доступен
- Fallback на legacy логику работает
- Параметры `resume={"id": ...}`, `vacancy`, `cover_letter` передаются корректно
- `search_profile_id=None` — правильно для данного use case?

**Проверить:** метод `_notify` вызывается? Обработка исключений корректна?

### 5. Добавить тесты

**Новые тесты (3-5 шт.):**
- Тест того, что `ApplyToVacanciesUseCase` использует адаптер когда он передан
- Тест того, что legacy fallback работает когда адаптера нет
- Тест создания `ApplicationDraftModel` в адаптере с правильными полями
- Тест депрекации в `apply_one.py` и `apply_worker.py`

**Исправить падающие тесты:**
- `test_apply_vacancies_ignores_unknown_keys` — вероятно, ломается из-за изменения сигнатуры конструктора `ApplyToVacanciesUseCase`
- `test_apply_to_vacancies_use_case_receives_factory` — проверяет передачу factory, возможно конфликт с новым параметром

### 6. Запуск валидации

```bash
cd /home/mikhail/projects/hh_apply_55
uv run --frozen pytest tests/ -q          # 0 failed
uv run --frozen ruff check src/ tests/    # 0 errors
```

---

## Чек-лист приёмки

| Пункт | Статус |
|-------|--------|
| `apply_one.py` — ruff clean (импорты вверху) | ❌ |
| `apply_worker.py` — добавлено DeprecationWarning | ❌ |
| `container.py` — адаптер корректно создаётся и передаётся | ❌ |
| `apply_to_vacancies.py` — использует адаптер, fallback работает | ❌ |
| Все тесты проходят (822+ тестов) | ❌ |
| Ruff clean на всём проекте | ❌ |
| Коммит с сообщением `refactor(application_submit): wire VSA slice into runtime (#55)` | ❌ |

---

## Зависимости для следующих задач

После завершения #55 можно начинать:
- **#56** Telegram Bot Switchover (зависит от #55)
- **#57** Channel Monitoring Switchover (зависит от #56)
- **#58** MAX Bot Switchover (зависит от #57)

---

## Полезные команды для отладки

```bash
# Проверить конкретный тест
uv run --frozen pytest tests/test_ui_api.py::TestErrorMessages::test_apply_vacancies_ignores_unknown_keys -v

# Проверить тест wiring
uv run --frozen pytest tests/test_vsa_vacancy_search_wiring.py::TestVacancySearchSliceWiring::test_apply_to_vacancies_use_case_receives_factory -v

# Ruff только на изменённых файлах
uv run --frozen ruff check src/hh_applicant_tool/services/apply_one.py src/hh_applicant_tool/container.py src/hh_applicant_tool/application/use_cases/apply_to_vacancies.py

# Посмотреть diff изменений
git -C /home/mikhail/projects/hh_apply_55 diff
```