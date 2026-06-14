# Project Roadmap

> Last updated: 2026-06-14 — see [issues](https://github.com/q-user/hh_apply/issues)
> for the authoritative task list. This document is a high-level summary,
> not a substitute for the issue tracker.

## Current State

**VSA foundation is in place**, but the migration is **not** complete.

- 7 VSA slices exist under `src/job_bot/` (`config_auth`, `vacancy_search`,
  `channel_monitoring`, `max_bot`, `telegram_bot`, `application_prep`,
  `application_submit`) plus a shared kernel (`src/job_bot/shared/`)
- 3 slices are fully "clean VSA" — no `hh_applicant_tool.*` imports at all
  (`channel_monitoring`, `config_auth`, `max_bot`)
- 1 slice is "near-clean" — a single handler (`vacancy_search_handler.py`)
  imports two error types from `hh_applicant_tool.api` (`BadResponse`,
  `ApiError`); all models, repositories, ports, and the other handlers
  are VSA-native
- 3 slices are "mixed" — VSA structure in place, but the slice still
  calls into the legacy package for transport/services: `telegram_bot`
  (6 files), `application_prep` (3 files), `application_submit` (3 files)
- Roughly 3 800 LOC of legacy `hh_applicant_tool/` use-case code in
  `application/use_cases/apply_to_vacancies.py` (1 118 LOC),
  `application/use_cases/prepare_vacancies.py` (689 LOC),
  `services/review_flow.py` (1 010 LOC), `services/relevance.py`
  (863 LOC), `services/daily_digest.py` (413 LOC),
  `services/applications.py` (292 LOC),
  `services/cover_letters.py` (214 LOC),
  `services/vacancy_search.py` (201 LOC) is not yet bridged
- Legacy `hh_applicant_tool/` is still the live entry point
  (`pyproject.toml` `packages`, `hh_applicant_tool.main:main`,
  `container.py` lazily instantiates the VSA slices)
- `main` is 119 commits behind `develop`; no open PR bridging them
- The CI block (issue #82) has been resolved: the latest `develop`
  is green for ruff, the MagicMockDigest pytest failure, and
  pre-commit. The mypy `strict = true` gate is also passing
  on `src/job_bot/` (per-file overrides in `pyproject.toml`)

## Phase A: Unblock CI — DONE

| Issue | Task | Status |
|-------|------|--------|
| #82 | Fix ruff errors + MagicMockDigest pytest failure on develop | Closed 2026-06-14 (#100 follow-up) |

Phase A is unblocked. `develop` is green for ruff, pytest, and the
mypy `strict` gate on `src/job_bot/`. This was a prerequisite for
every other VSA PR landing cleanly.

## Phase B: Complete VSA switchover — IN PROGRESS

Switchover slices are landed; the open work is the *use-case* bridges
and the final deprecation of legacy service code.

| Issue | Task | Status | Notes |
|-------|------|--------|-------|
| #50 | Architecture: Migrate to Vertical Slice Architecture | Closed 2026-06-10 | Foundation in place |
| #53 | Wire Vacancy Search slice + deprecate old code | Closed 2026-06-11 | Near-clean (2 legacy error imports) |
| #54 | Wire Application Prep slice + deprecate old code | Closed 2026-06-11 | Mixed: 3 files import from legacy |
| #55 | Wire Application Submit slice + deprecate old code | Closed 2026-06-11 | Mixed: 3 files import from legacy |
| #56 | Wire Telegram Bot slice + deprecate old code | Closed 2026-06-11 | Mixed: 6 files import from legacy |
| #57 | Wire Channel Monitoring slice + deprecate old code | Closed 2026-06-11 | Clean |
| #58 | Wire MAX Bot slice + deprecate old code | Closed 2026-06-11 | Clean |
| #77 | VSA switchover (port scaffolding, shim contracts) | Closed 2026-06-13 | Shims live in `hh_applicant_tool/container.py` |
| #59 | Wire Config/Auth slice + deprecate old code | Open | Slice is clean and wired in `AppContainer`; full legacy retirement pending |
| #76 | Remove deprecated telegram/channel/MAX service code | Open | Follow-up to PRs #56/#57/#58 |
| #87 | Bridge `review_flow.py` (1 010 LOC) to VSA | Open | Orchestrator still in legacy `services/` |
| #88 | Bridge `vacancy_tests.py` (294 LOC) to VSA | Open | **Note:** the legacy `vacancy_tests.py` file does not exist on `develop`; test-pipeline logic already lives at `src/job_bot/application_submit/handlers/test_handler.py`. The open work is removing the stale references and `services/applications.py` shim. |
| #89 | Bridge `apply_to_vacancies.py` (1 117 LOC) to VSA | Open | Orchestrator |
| #90 | Bridge `prepare_vacancies.py` (685 LOC) to VSA | Open | Orchestrator |

**Success criteria:** all 7 VSA slices are clean (zero legacy imports),
4 old orchestrator files are bridged, `hh_applicant_tool` is reduced
to a thin deprecation shim package that only re-exports the public
API for backward compatibility.

## Phase C: Standardize and clean — IN PROGRESS

| Issue | Task | Status |
|-------|------|--------|
| #64 | mypy strict on `src/job_bot/` | Closed 2026-06-11 (`strict = true` per-file override) |
| #65 | Performance benchmarks | Closed 2026-06-11 (`benchmarks/` lives at repo root) |
| #67 | Drop global event bus anti-pattern | Closed 2026-06-11 |
| #68 | Drop dead commented code / TODOs | Closed 2026-06-11 |
| #69 | Reduce bare `except Exception` in business logic | Closed 2026-06-11 (gated by `BLE001` rule, see `pyproject.toml`) |
| #70 | Track deprecation timeline for legacy services | Closed 2026-06-11 |
| #71 | Reduce `type: ignore[union-attr]` in `apply_to_vacancies.py` | Closed 2026-06-11 |
| #73 | Preserve `CaptchaRequired` / `LimitExceeded` semantics in adapter | Closed 2026-06-11 |
| #74 | Drop `StorageFacade(self._storage) # type: ignore[arg-type]` | Closed 2026-06-11 |
| #75 | Fix `fix(error-handling)` regressions | Closed 2026-06-11 |
| #63 | Integration tests | Closed 2026-06-14 (`tests/integration/` covers cross-slice flows) |
| #83 | Replace mypy with ty | Closed 2026-06-14 |
| #92 | Standardize deprecation contract for legacy VSA-shim modules | Open |
| #93 | Move cross-cutting utilities from `hh_applicant_tool/utils` to `job_bot/shared` | Open |
| #94 | Fix VSA test isolation: `tests/vsa/conftest.py` still uses legacy `StorageFacade` | Open |
| #95 | Delete dead code and wire or remove decorative `Settings` class | Open |
| #96 | Update VSA documentation (ROADMAP, migration guide, ui.md, README) | Open | This PR |

## Phase D: Production hardening (later)

- Observability: OpenTelemetry, structured logs, Prometheus metrics
- Health endpoints: `/health`, `/ready`
- Rate limiting per HH API endpoint
- Secrets management
- Sync `main` with `develop` (currently 119 commits behind)
- CI/CD improvements

## Timeline (rough estimate)

| Week | Focus |
|------|-------|
| 1 | Phase A (CI unblocked) |
| 2 | Phase B switchover slices |
| 3 | Phase B use-case bridges (#87, #88, #89, #90) — in progress |
| 4 | Phase C completion (#92, #93, #94, #95) — in progress |
| 5+ | Phase D (production hardening) and `main` ↔ `develop` reconciliation |

## Blockers

1. **#59 / #76** — `Config/Auth` slice wiring is complete but legacy
   service paths are not yet fully deprecated. Required before
   `hh_applicant_tool` can become a thin shim package.
2. **Main behind develop** — `main` is 119 commits behind, no PR
   bridging them. Decide: rebase merge, or fast-forward once the
   VSA bridge work lands.
3. **#92–#95** — the deprecation contract, `job_bot/shared` utility
   migration, VSA test isolation, and decorative `Settings` cleanup
   must land before `hh_applicant_tool/` can be safely deleted.
