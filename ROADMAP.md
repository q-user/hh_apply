# Project Roadmap

> Last updated: 2026-06-15 — see [issues](https://github.com/q-user/hh_apply/issues)
> for the authoritative task list. This document is a high-level summary,
> not a substitute for the issue tracker.

## Current State

**VSA migration is essentially complete.** All 7 slices are wired and
the 4 major bridge PRs plus the daily_digest bridge have been merged.
The legacy `hh_applicant_tool/` package is reduced to deprecation shims
plus the CLI/UI entry points that are the *only* reason it still ships.

- 7 VSA slices live under `src/job_bot/` (`application_prep`,
  `application_submit`, `channel_monitoring`, `config_auth`, `max_bot`,
  `telegram_bot`, `vacancy_search`) plus a shared kernel
  (`src/job_bot/shared/`)
- 4 major orchestrator bridges merged: `review_flow` (#87), `apply_to_vacancies`
  (#89), `prepare_vacancies` (#90), `config_auth` switchover (#59), and the
  `daily_digest` service bridge (#8) — see the **Completed** subsection
  below for the merged PRs
- All 7 slices are wired through `AppContainer` in
  `src/hh_applicant_tool/container.py` and exercised by the live CLI
  operations (`apply-worker`, `telegram-bot`, `channel-monitor`,
  `max-bot`, `apply-vacancies`, `prepare-vacancies`)
- `tests/vsa/` covers every slice in isolation, plus
  `tests/integration/` covers cross-slice flows (issue #63)
- Legacy `hh_applicant_tool/` is ~14K LOC, the bulk of which is now
  deprecation shims that re-export the VSA public API and emit a
  `DeprecationWarning` (contract enforced by
  `tests/test_issue_92_deprecation.py`)
- Validation: 1014 tests passed, 7 xfailed (expected), `ruff` clean,
  `mypy --strict` clean on `src/job_bot/`
- The CI block (issue #82) remains resolved

## Phase A: Unblock CI — DONE

| Issue | Task | Status |
|-------|------|--------|
| #82 | Fix ruff errors + MagicMockDigest pytest failure on develop | Closed 2026-06-14 (#100 follow-up) |

Phase A is unblocked. `develop` is green for ruff, pytest, and the
mypy `strict` gate on `src/job_bot/`. This was a prerequisite for
every other VSA PR landing cleanly.

## Phase B: Complete VSA switchover — DONE

All 7 slices are wired and every major orchestrator has a VSA bridge
plus a canonical deprecation shim. See the **Completed** subsection
below for the bridge PRs that landed this phase.

| Issue | Task | Status | Notes |
|-------|------|--------|-------|
| #50 | Architecture: Migrate to Vertical Slice Architecture | Closed 2026-06-10 | Foundation in place |
| #53 | Wire Vacancy Search slice + deprecate old code | Closed 2026-06-11 | Switchover complete |
| #54 | Wire Application Prep slice + deprecate old code | Closed 2026-06-11 | `relevance` / `cover_letters` / `applications` live as deprecation shims |
| #55 | Wire Application Submit slice + deprecate old code | Closed 2026-06-11 | VSA orchestrator + legacy `apply_to_vacancies` bridge |
| #56 | Wire Telegram Bot slice + deprecate old code | Closed 2026-06-11 | Bridge landed in #87 + #134 |
| #57 | Wire Channel Monitoring slice + deprecate old code | Closed 2026-06-11 | Clean |
| #58 | Wire MAX Bot slice + deprecate old code | Closed 2026-06-11 | Clean |
| #77 | VSA switchover (port scaffolding, shim contracts) | Closed 2026-06-13 | Shims live in `hh_applicant_tool/container.py` |
| #59 | Wire Config/Auth slice + deprecate old code | Closed 2026-06-14 | PR #131; `utils.config` + `operations.authorize` are shims |
| #76 | Remove deprecated telegram/channel/MAX service code | Closed 2026-06-14 | PR #123; only deprecation shims remain |
| #87 | Bridge `review_flow.py` to VSA | Closed 2026-06-14 | PR #132; `services/review_flow.py` is a shim |
| #88 | Remove `services/applications.py` vacancy_tests shim | Closed 2026-06-13 | Test pipeline lives in `application_submit/handlers/test_handler.py` |
| #89 | Bridge `apply_to_vacancies.py` to VSA orchestration | Closed 2026-06-14 | PR #129; partial bridge, per-phase handlers to follow |
| #90 | Bridge `prepare_vacancies.py` to VSA orchestration | Closed 2026-06-14 | PR #130; `application_prep/slice.py` is the orchestrator |

**Success criteria — met.** All 7 VSA slices are wired; every legacy
orchestrator that lived in `src/hh_applicant_tool/{services,application/use_cases}/`
either moved to a VSA slice or is a thin deprecation shim with a
standardised contract (issue #92).

### Completed (Phase B bridge PRs)

| PR | Issue | Summary |
|----|-------|---------|
| #131 | #59 | `config_auth` switchover — `utils.config` and `operations.authorize` became shims; `AppContainer` instantiates `ConfigAuthSlice` |
| #132 | #87 | `review_flow` bridge — moved to `job_bot.telegram_bot.services.review_service`; `services/review_flow.py` reduced to a shim |
| #129 | #89 | `apply_to_vacancies` bridge — `ApplicationSubmitSlice.run_apply_pipeline` is the new top-level orchestrator, the use case delegates to the slice when wired |
| #130 | #90 | `prepare_vacancies` bridge — `ApplicationPrepSlice` is the new orchestrator; container wires it into `PrepareVacanciesUseCase` |
| #134 | #8 | `daily_digest` bridge — `DailyDigestService` moved to `job_bot.telegram_bot.services.daily_digest_service`; `services/daily_digest.py` is a shim |

### Remaining (next major work item)

The next follow-up is **deprecation shim removal** — once external
consumers (docs, scripts, third-party forks) have had a release cycle
on the VSA public API, the shim modules in `src/hh_applicant_tool/`
can be deleted and the entry point can switch to a VSA-native
`__main__`. Tracked as the Phase D work item below; see also
issue #88's follow-up for `services/applications.py`.

## Phase C: Standardize and clean — DONE

All standardised, clean-up work has landed. The remaining items in
the old checklist (#92–#96) were closed in PRs #122–#127.

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
| #92 | Standardize deprecation contract for legacy VSA-shim modules | Closed 2026-06-14 (PR #126; enforced by `tests/test_issue_92_deprecation.py`) |
| #93 | Move cross-cutting utilities from `hh_applicant_tool/utils` to `job_bot/shared` | Closed 2026-06-14 (PR #127) |
| #94 | Fix VSA test isolation: `tests/vsa/conftest.py` still uses legacy `StorageFacade` | Closed 2026-06-14 (PR #122) |
| #95 | Delete dead code and wire or remove decorative `Settings` class | Closed 2026-06-14 (PR #125) |
| #96 | Update VSA documentation (ROADMAP, migration guide, ui.md, README) | Closed 2026-06-14 (PR #124) |

## Phase D: Deprecation shim removal — NEXT

This is the next major work item. The legacy `hh_applicant_tool/`
package is fully shimmed, so the remaining work is purely deletion
plus a small entry-point migration.

- Decide on the removal timeline (target a major version bump, e.g. 2.0)
- Delete deprecation shim modules in `src/hh_applicant_tool/{services,utils,operations,application}/`
  whose public API has been replaced by VSA (`services.applications`,
  `services.cover_letters`, `services.relevance`, `services.vacancy_search`,
  `services.daily_digest`, `services.review_flow`, `utils.config`,
  `operations.authorize`)
- Replace `hh_applicant_tool.main:main` with a VSA-native
  `__main__` that constructs `AppContainer` and dispatches CLI ops
  directly; the `hh_applicant_tool` distribution package can be kept
  as a thin entry-point shim or retired entirely
- Update `pyproject.toml` `packages` and entry-points accordingly
- Audit and remove the corresponding legacy tests under `tests/test_*.py`
  that exercise the shims (kept today only as contract tests)

## Phase E: Production hardening (later)

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
| 3 | Phase B use-case bridges (#87, #88, #89, #90) |
| 4 | Phase C completion (#92–#96) |
| 5+ | Phase D (deprecation shim removal) and `main` ↔ `develop` reconciliation; Phase E (production hardening) |

## Blockers

1. **Phase D entry-point decision** — the deprecation shim removal
   needs a target version (likely 2.0) and a documented migration
   path for any external consumers importing from
   `hh_applicant_tool.*` directly. Required before the
   `hh_applicant_tool` distribution package can be retired.
2. **Main behind develop** — `main` is still 119 commits behind
   `develop`. Decide: rebase merge, or fast-forward once Phase D
   lands. The deprecation shim removal is a natural cut line for
   the next release.
3. **Per-phase handlers in `application_submit`** — the
   `apply_to_vacancies` bridge (PR #129) is a partial bridge: the
   slice is the entry point, but individual phases (search, score,
   cover letter, filter, email, captcha, storage I/O) still live
   inline in the use case. Each phase is a candidate for a future
   follow-up issue that ports it to a dedicated handler.
