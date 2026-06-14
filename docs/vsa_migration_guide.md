# Vertical Slice Architecture Migration Guide

## Overview

This document describes the migration from Clean Architecture (horizontal layers) to Vertical Slice Architecture (feature-based) for the HH Applicant Tool project.

## Legacy Architecture (Clean Architecture, still in place)

```
src/hh_applicant_tool/
├── application/          # Use cases (horizontal)
│   ├── use_cases/
│   ├── dto.py
│   └── ports.py
├── services/             # Domain services (horizontal)
├── infrastructure/       # Infrastructure implementations (horizontal)
├── storage/              # Database & repositories (horizontal)
├── telegram/             # Telegram bot
├── operations/           # CLI operations
├── api/                  # HH API client
├── ai/                   # AI clients
├── ui/                   # UI components
├── main.py               # HHApplicantTool (entry point, still active)
└── container.py          # AppContainer — composition root, lazily wires VSA slices
```

**Problems with the legacy architecture:**
- Cross-cutting changes require touching multiple layers
- Hard to understand feature boundaries
- God class (`HHApplicantTool`) with too many responsibilities
- Difficult to test features in isolation
- Not agent-friendly for AI-assisted development

## Target Architecture (Vertical Slice Architecture)

The VSA package lives at `src/job_bot/`. Every feature is a *self-contained slice* with its own `models/`, `handlers/`, `ports/`, and (where applicable) `services/` and `repositories/` subpackages, plus a `slice.py` factory and a public `__init__.py`.

```
src/job_bot/
├── shared/                    # Shared kernel (cross-slice)
│   ├── storage/               # Database, base repository
│   ├── api/                   # HH API client
│   ├── ai/                    # AI client
│   ├── events/                # Event bus (deprecated, see issue #67)
│   └── config/                # Settings
├── vacancy_search/            # Slice: search profiles, HH API
│   ├── models/                # SearchProfile, Vacancy
│   ├── repositories/          # SearchProfileRepo, VacancyRepo
│   ├── handlers/              # Business logic handlers
│   ├── ports/                 # Interfaces for other slices
│   ├── slice.py               # Factory & main entry point
│   └── __init__.py            # Public API
├── application_prep/          # Slice: drafts, relevance, cover letters
│   ├── models/  handlers/  ports/  repositories/  slice.py
├── application_submit/        # Slice: apply worker, tests, retry
│   ├── models/  handlers/  ports/  services/  slice.py
├── telegram_bot/              # Slice: commands, digest, review
│   ├── models/  handlers/  ports/  services/  slice.py
├── channel_monitoring/        # Slice: TG channel polling
│   ├── models/  handlers/  ports/  services/  slice.py
├── max_bot/                   # Slice: MAX messenger
│   ├── models/  handlers/  ports/  services/  slice.py
└── config_auth/               # Slice: config, OAuth, users
    ├── models/  handlers/  ports/  slice.py
```

> **Layout note:** `config_auth` has no `repositories/` (config is
> in-memory, persisted as JSON) and no `services/` subpackage — that
> is intentional, not a missing directory.

## Slice Status (as of 2026-06-14)

| Slice | Status | Legacy imports | Issue |
|-------|--------|----------------|-------|
| `vacancy_search` | Near-clean | 1 file (`BadResponse`, `ApiError` from `hh_applicant_tool.api`) | #53 (closed), #88 partial |
| `application_prep` | Mixed | 3 files (AI client, string utils, model shim) | #54 (closed), #89 partial |
| `application_submit` | Mixed | 3 files (storage models, API errors, time/delay infra) | #55 (closed), #89 partial |
| `telegram_bot` | Mixed | 6 files (transport, daily digest, review flow, storage facade) | #56 (closed), #87 partial |
| `channel_monitoring` | Clean | 0 files | #57 (closed) |
| `max_bot` | Clean | 0 files | #58 (closed) |
| `config_auth` | Clean | 0 files | #59 (open — legacy retirement pending) |

The three legacy orchestrators that still live in `src/hh_applicant_tool/`
and need to be bridged to VSA are tracked by:

- **#87** — `services/review_flow.py` (1 010 LOC) → `telegram_bot`
- **#88** — `services/applications.py` (the `vacancy_tests` shim) → `application_submit`
- **#89** — `application/use_cases/apply_to_vacancies.py` (1 118 LOC) → `application_submit`
- **#90** — `application/use_cases/prepare_vacancies.py` (689 LOC) → `application_prep`

## Migration Strategy: Strangler Fig Pattern

### Phase 1: Foundation (Done, 2026-06-10 — issue #50)

1. ✅ Create new package structure: `src/job_bot/`
2. ✅ Create shared kernel packages
3. ✅ Create slice directories for all 7 bounded contexts
4. ✅ Implement pilot slice: `vacancy_search`
5. ✅ Write tests for pilot slice (`tests/vsa/test_vacancy_search_slice.py`)

### Phase 2: Extraction (Done, 2026-06-11 — issues #53–#58)

1. ✅ `vacancy_search` (issue #53)
2. ✅ `config_auth` (issue #50 pilot, full switchover in #59)
3. ✅ `telegram_bot` (issue #56)
4. ✅ `application_prep` (issue #54)
5. ✅ `application_submit` (issue #55)
6. ✅ `channel_monitoring` (issue #57)
7. ✅ `max_bot` (issue #58)

### Phase 3: Bridge & Integration (In progress)

1. ✅ CLI entry points — `hh_applicant_tool.operations.apply_worker` now wires
   `ApplicationSubmitSlice` via `AppContainer` (issue #77, closed 2026-06-13).
   Other operations (`channel_monitor`, `max_bot`, `telegram_bot`) follow
   the same pattern.
2. 🟡 Telegram bot — the slice is wired but the legacy `services/daily_digest.py`
   and `services/review_flow.py` are still the engines behind the slice
   adapters. Bridging happens in issue #87.
3. 🟡 UI — `hh_applicant_tool/ui/api.py` still depends on the legacy
   `AppContainer` + `HHApplicantTool` facade. There is no VSA UI slice
   yet; the legacy UI is a thin wrapper that calls `AppContainer.*` use
   cases, so it works through the VSA-bridged container.
4. 🟡 Deprecate old `hh_applicant_tool` package — blocked on issues
   #59, #76, #87, #88, #89, #90.

## Slice Design Principles

### 1. Each slice is self-contained
- Own models (domain entities)
- Own repositories (data access)
- Own handlers (business logic)
- Own ports (interfaces for other slices)

### 2. Cross-slice communication via ports
```python
# Other slices depend on ports, not implementations
from job_bot.vacancy_search.ports import VacancySearchPort

class ApplicationPrepHandler:
    def __init__(self, vacancy_search: VacancySearchPort):
        self._vacancy_search = vacancy_search
```

### 3. Shared kernel for common infrastructure
- Database connections (`src/job_bot/shared/storage/`)
- HH API client (`src/job_bot/shared/api/`)
- AI client (`src/job_bot/shared/ai/`)
- Settings (`src/job_bot/shared/config/`)

### 4. Dependency injection via factories
```python
# Factory creates slice with all dependencies wired
from job_bot.vacancy_search import create_vacancy_search_slice

slice_ = create_vacancy_search_slice()
profile = slice_.search_profiles.create_profile(
    SearchProfileCreate(name="Python Jobs", keywords="python")
)
```

## Pilot Slice: vacancy_search

### Structure (as it exists on `develop`)
```
vacancy_search/
├── models/
│   ├── __init__.py
│   ├── search_profile.py    # SearchProfile, SearchProfileCreate, SearchProfileUpdate
│   └── vacancy.py           # Vacancy, VacancyCreate
├── repositories/
│   ├── __init__.py
│   ├── search_profile_repo.py
│   └── vacancy_repo.py
├── handlers/
│   ├── __init__.py
│   ├── search_profile_handler.py
│   ├── vacancy_handler.py
│   └── vacancy_search_handler.py
├── ports/
│   ├── __init__.py
│   ├── search_profile_port.py
│   ├── vacancy_port.py
│   └── vacancy_search_port.py
├── slice.py                 # Factory & main entry
└── __init__.py              # Public API
```

### Usage Example
```python
from job_bot.shared.config.settings import load_settings
from job_bot.vacancy_search import create_vacancy_search_slice

# Load settings
settings = load_settings()

# Create slice (wires all dependencies)
slice_ = create_vacancy_search_slice(settings=settings)

# Use search profiles
profile = slice_.search_profiles.create_profile(
    SearchProfileCreate(name="Python Jobs", keywords="python")
)

# Search vacancies (requires OAuth token)
vacancies = slice_.search.search_vacancies(profile, access_token="...")

# Access stored vacancies
all_vacancies = slice_.vacancies.list_vacancies()
```

## Ports (Interfaces)

Each slice defines ports for other slices to use:

| Slice | Ports |
|-------|-------|
| vacancy_search | SearchProfilePort, VacancyPort, VacancySearchPort |
| application_prep | ApplicationPort, CoverLetterPort, RelevancePort, RelevanceStoragePort |
| application_submit | JobPort, ApplyOnePort, TestPort |
| telegram_bot | TelegramTransportPort, DailyDigestPort, ReviewFlowPort |
| channel_monitoring | ChannelPort, NotificationPort |
| max_bot | MaxTransportPort |
| config_auth | ConfigPort, AuthPort, UserPort |

## Testing Strategy

### Unit Tests (per slice)
- Test handlers in isolation with mocked repositories
- Test repository CRUD operations
- Test model validation

### Integration Tests (per slice)
- Test full slice with real database
- Test port implementations

### Cross-Slice Tests
- `tests/integration/` — end-to-end cross-slice flows (closed issue #63).
  Run with `pytest -m integration`.
- Port contract tests live next to the slice tests in `tests/vsa/`.

## Running Tests

```bash
# Run all tests
uv run --frozen pytest tests/ -q

# Run VSA slice tests
uv run --frozen pytest tests/vsa/ -v

# Run cross-slice integration tests (opt-in marker)
uv run --frozen pytest -m integration -v

# Run linting
uv run --frozen ruff check src/

# Run mypy (strict on src/job_bot/)
uv run --frozen mypy src/
```

## Migration Checklist

### Phase 1: Foundation — DONE (#50)
- [x] Create `src/job_bot/` package structure
- [x] Create shared kernel (storage, api, ai, config)
- [x] Create 7 slice directories
- [x] Implement `vacancy_search` pilot slice
- [x] Write tests for `vacancy_search` slice
- [x] `events/` subpackage created (now deprecated, see #67)

### Phase 2: Extraction — DONE (#53, #54, #55, #56, #57, #58)
- [x] `vacancy_search` (issue #53)
- [x] `config_auth` (issue #50, switchover pending in #59)
- [x] `telegram_bot` (issue #56)
- [x] `application_prep` (issue #54)
- [x] `application_submit` (issue #55)
- [x] `channel_monitoring` (issue #57)
- [x] `max_bot` (issue #58)

### Phase 3: Integration — IN PROGRESS
- [x] CLI `apply-worker` rewired to `ApplicationSubmitSlice` (issue #77)
- [x] CLI `channel-monitor` rewired to `ChannelMonitorSlice` (issue #57)
- [x] CLI `max-bot` rewired to `MaxBotSlice` (issue #58)
- [x] CLI `telegram-bot` rewired to `TelegramBotSlice` (issue #56)
- [x] `AppContainer` lazily instantiates all 7 slices (issue #77)
- [x] Settings unified through `ConfigAuthSlice` (issue #59 partial)
- [ ] Bridge `services/review_flow.py` to `telegram_bot` (issue #87)
- [ ] Bridge `services/daily_digest.py` to `telegram_bot` (alongside #87)
- [ ] Bridge `services/relevance.py` to `application_prep`
- [ ] Bridge `services/vacancy_search.py` to `vacancy_search`
- [ ] Bridge `services/cover_letters.py` to `application_prep`
- [ ] Bridge `application/use_cases/apply_to_vacancies.py` to `application_submit` (issue #89)
- [ ] Bridge `application/use_cases/prepare_vacancies.py` to `application_prep` (issue #90)
- [ ] Remove `services/applications.py` vacancy_tests shim (issue #88)
- [ ] Deprecate `hh_applicant_tool` package (blocked on #59, #76, #87–#90)

## Benefits of VSA

1. **Agent-friendly**: Clear feature boundaries for AI-assisted development
2. **Testable**: Each slice can be tested in isolation
3. **Maintainable**: Changes to one feature don't affect others
4. **Scalable**: Easy to add new slices
5. **Deployable**: Slices can be deployed independently (future)
6. **Understandable**: Feature-centric organization matches mental model

## References

- [Vertical Slice Architecture](https://www.youtube.com/watch?v=Vgv1tD5QqJ8) — Jimmy Bogard
- [Feature Folders in FastAPI](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- [Clean Architecture vs Vertical Slices](https://www.milanjovanovic.tech/blog/clean-architecture-vs-vertical-slice-architecture)
- Internal VSA conventions used in this repo are documented in the project's agent skills (see `.agents/skills/vertical-slice-python/`; local-only, not part of the published tree).
