# Vertical Slice Architecture Migration Guide

## Overview

This document describes the migration from Clean Architecture (horizontal layers) to Vertical Slice Architecture (feature-based) for the hh_apply project.

## Current Architecture (Clean Architecture)

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
└── main.py               # HHApplicantTool (god class)
```

**Problems with current architecture:**
- Cross-cutting changes require touching multiple layers
- Hard to understand feature boundaries
- God class (`HHApplicantTool`) with too many responsibilities
- Difficult to test features in isolation
- Not agent-friendly for AI-assisted development

## Target Architecture (Vertical Slice Architecture)

```
src/job_bot/
├── shared/                    # Shared kernel (cross-slice)
│   ├── storage/               # Database, base repository
│   ├── api/                   # HH API client
│   ├── ai/                    # AI client
│   ├── events/                # Event bus
│   └── config/                # Settings
├── vacancy_search/            # Slice 1: Search profiles, HH API
│   ├── models/                # SearchProfile, Vacancy
│   ├── repositories/          # SearchProfileRepo, VacancyRepo
│   ├── handlers/              # Business logic handlers
│   ├── ports/                 # Interfaces for other slices
│   └── slice.py               # Factory & main entry point
├── application_prep/          # Slice 2: Drafts, relevance, cover letters
├── application_submit/        # Slice 3: Apply worker, tests
├── telegram_bot/              # Slice 4: Commands, digest, review
├── channel_monitoring/        # Slice 5: TG channel polling
├── max_bot/                   # Slice 6: MAX messenger
└── config_auth/               # Slice 7: Config, OAuth, users
```

## Migration Strategy: Strangler Fig Pattern

### Phase 1: Foundation (Current)
1. ✅ Create new package structure: `src/job_bot/`
2. ✅ Create shared kernel packages
3. ✅ Create slice directories for all 7 bounded contexts
4. ✅ Implement pilot slice: `vacancy_search`
5. ✅ Write tests for pilot slice

### Phase 2: Extraction (Future)
1. Extract `config_auth` slice from existing config/OAuth code
2. Extract `telegram_bot` slice from telegram operations
3. Extract `application_prep` from services/cover_letters, relevance
4. Extract `application_submit` from apply_worker, apply_one
4. Extract `channel_monitoring` from new channel polling code
5. Extract `max_bot` from new MAX integration code

### Phase 3: Integration (Future)
1. Update CLI entry points to use new slices
2. Update Telegram bot to use new slices
3. Update UI to use new slices
4. Deprecate old `hh_applicant_tool` package

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
- Database connections
- HH API client
- AI client
- Event bus
- Configuration

### 4. Dependency injection via factories
```python
# Factory creates slice with all dependencies wired
slice = create_vacancy_search_slice(settings=settings)
```

## Pilot Slice: vacancy_search

### Structure
```
vacancy_search/
├── models/
│   ├── __init__.py
│   ├── search_profile.py    # SearchProfile, Create, Update
│   └── vacancy.py           # Vacancy, Create
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
slice = create_vacancy_search_slice(settings=settings)

# Use search profiles
profile = slice.search_profiles.create_profile(
    SearchProfileCreate(name="Python Jobs", keywords="python")
)

# Search vacancies (requires OAuth token)
vacancies = slice.search.search_vacancies(profile, access_token="...")

# Access stored vacancies
all_vacancies = slice.vacancies.list_vacancies()
```

## Ports (Interfaces)

Each slice defines ports for other slices to use:

| Slice | Ports |
|-------|-------|
| vacancy_search | SearchProfilePort, VacancyPort, VacancySearchPort |
| application_prep | DraftPort, RelevancePort, CoverLetterPort |
| application_submit | ApplyPort, TestPort |
| telegram_bot | CommandPort, DigestPort, ReviewPort |
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
- Test port contracts between slices
- Test event bus communication

## Running Tests

```bash
# Run all tests (must stay 561+)
uv run --frozen pytest tests/ -q

# Run VSA pilot slice tests
uv run --frozen pytest tests/vsa/ -v

# Run linting
uv run --frozen ruff check src/
```

## Migration Checklist

### Phase 1: Foundation
- [x] Create `src/job_bot/` package structure
- [x] Create shared kernel (storage, api, ai, events, config)
- [x] Create 7 slice directories
- [x] Implement `vacancy_search` pilot slice
- [x] Write tests for `vacancy_search` slice
- [ ] Verify existing tests still pass (561+)

### Phase 2: Extraction
- [x] Extract `config_auth` slice (Issue #50)
- [ ] Extract `telegram_bot` slice
- [ ] Extract `application_prep` slice
- [ ] Extract `application_submit` slice
- [ ] Extract `channel_monitoring` slice
- [ ] Extract `max_bot` slice

### Phase 3: Integration
- [ ] Update CLI to use slices
- [ ] Update Telegram bot to use slices
- [ ] Update UI to use slices
- [ ] Deprecate `hh_applicant_tool` package

## Benefits of VSA

1. **Agent-friendly**: Clear feature boundaries for AI-assisted development
2. **Testable**: Each slice can be tested in isolation
3. **Maintainable**: Changes to one feature don't affect others
4. **Scalable**: Easy to add new slices
5. **Deployable**: Slices can be deployed independently (future)
6. **Understandable**: Feature-centric organization matches mental model

## References

- [Vertical Slice Architecture](https://www.youtube.com/watch?v=Vgv1tD5QqJ8) - Jimmy Bogard
- [Feature Folders in FastAPI](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- [Clean Architecture vs Vertical Slices](https://www.milanjovanovic.tech/blog/clean-architecture-vs-vertical-slice-architecture)