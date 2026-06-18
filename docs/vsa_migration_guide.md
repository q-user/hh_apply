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

## Slice Status (as of 2026-06-15)

All 7 VSA slices are wired. The bulk of the legacy orchestrator code
(`services/review_flow`, `services/daily_digest`,
`application/use_cases/apply_to_vacancies`,
`application/use_cases/prepare_vacancies`, and the `config_auth`
service paths) has been bridged to VSA in PRs #129–#134 — the
remaining legacy modules in `src/hh_applicant_tool/services/` and
`src/hh_applicant_tool/application/use_cases/` are now deprecation
shims with a standardised `DeprecationWarning` contract (issue #92).
The next step is *deleting* the shims (tracked in [issues](https://github.com/q-user/hh_apply/issues),
Phase D); see also the [shim contract table in
`tests/test_issue_92_deprecation.py`](../tests/test_issue_92_deprecation.py)
for the canonical list of shims still in place.

| Slice | Status | Notes |
|-------|--------|-------|
| `vacancy_search` | Wired | Backing `services/vacancy_search.py` is a deprecation shim (issue #53) |
| `application_prep` | Wired | Backing `services/{relevance,cover_letters,applications}` and `use_cases/prepare_vacancies` are deprecation shims (#54, #130) |
| `application_submit` | Wired | `ApplicationSubmitSlice.run_apply_pipeline` is the new orchestrator; `use_cases/apply_to_vacancies` is a partial bridge (#55, #129) |
| `telegram_bot` | Wired | `services/{review_flow,daily_digest}` moved to VSA services and the legacy modules are shims (#56, #87, #8) |
| `channel_monitoring` | Wired | No remaining legacy code paths (#57) |
| `max_bot` | Wired | No remaining legacy code paths (#58) |
| `config_auth` | Wired | `utils.config` and `operations.authorize` are deprecation shims (#59) |

### Completed bridge PRs (Phase B)

| PR | Issue | Bridge |
|----|-------|--------|
| #131 | #59 | `config_auth` switchover |
| #132 | #87 | `services/review_flow` → `telegram_bot` |
| #129 | #89 | `use_cases/apply_to_vacancies` → `application_submit` |
| #130 | #90 | `use_cases/prepare_vacancies` → `application_prep` |
| #134 | #8 | `services/daily_digest` → `telegram_bot` |

### Remaining

The remaining work is the deprecation shim removal — see [issues](https://github.com/q-user/hh_apply/issues),
Phase D. The shim modules listed in `tests/test_issue_92_deprecation.py`
(`SHIM_CONTRACT`) are the candidates for deletion once a removal
timeline is set.

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

### Phase 3: Bridge & Integration (Done, 2026-06-14 — issues #59, #87, #89, #90, #8)

1. ✅ CLI entry points — `hh_applicant_tool.operations.apply_worker` wires
   `ApplicationSubmitSlice` via `AppContainer` (issue #77). The other
   operations (`channel-monitor`, `max-bot`, `telegram-bot`) follow
   the same pattern.
2. ✅ Telegram bot — `services/review_flow.py` and `services/daily_digest.py`
   moved to `job_bot.telegram_bot.services.*` (issues #87, #8).
3. 🟡 UI — `hh_applicant_tool/ui/api.py` still depends on the legacy
   `AppContainer` + `HHApplicantTool` facade. There is no VSA UI slice
   yet; the legacy UI is a thin wrapper that calls `AppContainer.*` use
   cases, so it works through the VSA-bridged container.
4. 🟡 Deprecate old `hh_applicant_tool` package — the shims are
   standardised (issue #92) and the slices are wired, but the shim
   modules themselves are still in place. Deletion is the next major
   work item ([issues](https://github.com/q-user/hh_apply/issues), Phase D).

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

### Phase 3: Bridge & Integration — DONE (#59, #76, #87, #88, #89, #90, #8)
- [x] CLI `apply-worker` rewired to `ApplicationSubmitSlice` (issue #77)
- [x] CLI `channel-monitor` rewired to `ChannelMonitorSlice` (issue #57)
- [x] CLI `max-bot` rewired to `MaxBotSlice` (issue #58)
- [x] CLI `telegram-bot` rewired to `TelegramBotSlice` (issue #56)
- [x] `AppContainer` lazily instantiates all 7 slices (issue #77)
- [x] Settings unified through `ConfigAuthSlice` (issue #59, PR #131)
- [x] Bridge `services/review_flow.py` to `telegram_bot` (issue #87, PR #132)
- [x] Bridge `services/daily_digest.py` to `telegram_bot` (issue #8, PR #134)
- [x] Bridge `application/use_cases/apply_to_vacancies.py` to `application_submit` (issue #89, PR #129)
- [x] Bridge `application/use_cases/prepare_vacancies.py` to `application_prep` (issue #90, PR #130)
- [x] Remove deprecated telegram/channel/MAX service code (issue #76, PR #123)
- [x] Standardise deprecation contract for shim modules (issue #92, PR #126)
- [x] Move cross-cutting utilities to `job_bot/shared` (issue #93, PR #127)
- [x] Migrate `tests/vsa/conftest.py` to VSA storage (issue #94, PR #122)
- [x] Delete dead code and decorative `Settings` class (issue #95, PR #125)
- [x] Update VSA documentation (issue #96, PR #124)

### Phase 4: Deprecation shim removal — NEXT

- [ ] Decide on the removal timeline (target a major version bump, e.g. 2.0)
- [ ] Delete deprecation shim modules listed in
      `tests/test_issue_92_deprecation.py` `SHIM_CONTRACT` once the
      public API has been stable for a release cycle
- [ ] Replace `hh_applicant_tool.main:main` with a VSA-native `__main__`
- [ ] Audit and remove legacy tests under `tests/test_*.py` that
      exercise the shims (kept today only as contract tests)
- [ ] Sync `main` with `develop` (the natural cut line for the next
      release)

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
