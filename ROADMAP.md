# Project Roadmap

> Last updated: 2026-06-18 — see [issues](https://github.com/q-user/hh_apply/issues)
> for the authoritative task list. This document is a high-level summary,
> not a substitute for the issue tracker.

## Current State

**VSA migration is complete and shipped as 2.0.0.** All 7 slices are
wired, the legacy `hh_applicant_tool/` distribution package has been
retired from the published wheel, and `v2.0.0` is tagged on `main`
(commit `a13c9ef`, "Release 2.0.0 — hh_applicant_tool retired").

- 7 VSA slices live under `src/job_bot/` (`application_prep`,
  `application_submit`, `channel_monitoring`, `config_auth`, `max_bot`,
  `telegram_bot`, `vacancy_search`) plus a shared kernel
  (`src/job_bot/shared/`)
- `src/hh_applicant_tool/` is reduced to a **22-LOC deprecation shim
  across two files** (`__init__.py` 10 LOC, `__main__.py` 12 LOC). The
  directory is retained for in-tree `import hh_applicant_tool` (e.g.
  test fixtures) but is **no longer shipped in the wheel** (PR #192).
  Full directory removal is gated on the `main` ↔ `develop`
  reconciliation (Blocker #1).
- Entry point: `[project.scripts] hh-applicant-tool = "job_bot.cli.main:main"`
  (since PR #170). `pyproject.toml [tool.poetry] packages` ships only
  `job_bot`. UI templates (`src/job_bot/ui/templates/`) and SQL schema
  (`src/job_bot/_legacy_compat/storage/queries/**/*.sql`) are both in
  `[tool.poetry] include`.
- Version: `2.0.0` in `pyproject.toml`. Tag `v2.0.0` is at `a13c9ef`
  on `main`; tag description: "Release 2.0.0 — hh_applicant_tool
  retired".
- Validation: `uv run pytest tests/ -q --timeout=60` collects **1256
  tests** (2 pre-existing failures in `TestCaptchaHandlerLegacyFallback`,
  see Known issues). `ruff check .` is clean. `uv run ty check
  src/job_bot/` reports pre-existing diagnostics in
  `src/job_bot/telegram_bot/services/review_service.py` (unrelated to
  this PR; see Known issues).
- `main` is **11 commits behind** `develop` (down from 119). See
  Blockers.

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
| #89 | Bridge `apply_to_vacancies.py` to VSA orchestration | Closed 2026-06-14 | PR #129; followed up by PR #166 (Refs #145) |
| #90 | Bridge `prepare_vacancies.py` to VSA orchestration | Closed 2026-06-14 | PR #130; `application_prep/slice.py` is the orchestrator |

**Success criteria — met** (historical marker).

### Completed (Phase B bridge PRs)

| PR | Issue | Summary |
|----|-------|---------|
| #131 | #59 | `config_auth` switchover — `utils.config` and `operations.authorize` became shims; `AppContainer` instantiates `ConfigAuthSlice` |
| #132 | #87 | `review_flow` bridge — moved to `job_bot.telegram_bot.services.review_service`; `services/review_flow.py` reduced to a shim |
| #129 | #89 | `apply_to_vacancies` bridge — `ApplicationSubmitSlice.run_apply_pipeline` is the new top-level orchestrator, the use case delegates to the slice when wired |
| #130 | #90 | `prepare_vacancies` bridge — `ApplicationPrepSlice` is the new orchestrator; container wires it into `PrepareVacanciesUseCase` |
| #134 | #8 | `daily_digest` bridge — `DailyDigestService` moved to `job_bot.telegram_bot.services.daily_digest_service`; `services/daily_digest.py` is a shim |
| #166 | #145 | Per-phase handler extraction (WIP) — search/score/cover-letter/skip/email/captcha moved into `ApplicationSubmitSlice` handlers |

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

## Phase D: Deprecation shim removal — DONE

Phase D landed across 15 PRs. The legacy `hh_applicant_tool/` package
is reduced to a 22-LOC, two-file deprecation shim (`__init__.py` 10
LOC, `__main__.py` 12 LOC) and is no longer shipped in the wheel.
The wheel now contains only the `job_bot` package; the entry point
switched to `job_bot.cli.main:main` (PR #170). v2.0.0 is tagged on
`main`.

The two-file stub is intentionally retained for in-tree imports
(e.g. test fixtures). Full directory removal (i.e. dropping the
`src/hh_applicant_tool/` directory entirely) is gated on the
`main` ↔ `develop` reconciliation (Blocker #1) and is not a
precondition for the 2.0.0 release.

| Issue | PR | Summary |
|-------|----|---------|
| #157 | #168 | `chore: bump version to 2.0.0` (SemVer major) |
| #154 | #170 | `feat(vsa): add VSA-native __main__.py and switch [project.scripts]` |
| #158 | #174 | `refactor(vsa): delete hh_applicant_tool package, leave 5-line stub` |
| #159 | #175 | `chore(tests): remove dead trigger functions from test_issue_92_deprecation` |
| #151 | #167 | `refactor(vsa): port utils/{cookiejar,terminal,resume_md} to shared/utils and resume_management.services` |
| #152 | #162 | `refactor(vsa): port api/datatypes.py and api/errors.py to shared/api` |
| #153 | #169 | `refactor(vsa): port infrastructure/* to shared and per-slice services` |
| #150 | #171 | `refactor(vsa): decouple ui/api.py via UiApiContext port` |
| #155 | #172 | `refactor(vsa): slim AppContainer to a pure VSA composition root` |
| #176 | #182 | `fix(build): ship job_bot package in the published wheel` |
| #189 | #192 | `fix(wheel): ship VSA UI templates, drop stale hh_applicant_tool entries from pyproject.toml` |
| #188 | #191 | `fix(legacy-compat): tighten __getattr__ descriptor handling and db case` |
| #190 | #193 | `fix(migration-script): regex handles trailing comments, parenthesised imports, fixture skip` |
| #195 | #196 | `test: skip sixel test when Pillow missing` |
| #194 | #197 | `chore(legacy-compat): use inspect.getattr_static for inherited descriptor lookup` |

## Phase E: Production hardening — NEXT (optional)

Phase E was previously listed as "later". With Phases A–D done and
2.0.0 released, it is the next *intentional* work stream — not on
the critical path. None of these items block further development.

- Observability: OpenTelemetry, structured logs, Prometheus metrics
- Health endpoints: `/health`, `/ready`
- Rate limiting per HH API endpoint
- Secrets management
- CI/CD improvements

## Timeline (rough estimate)

| Week | Focus |
|------|-------|
| 1 | Phase A (CI unblocked) |
| 2 | Phase B switchover slices |
| 3 | Phase B use-case bridges (#87, #88, #89, #90) |
| 4 | Phase C completion (#92–#96) |
| 5 | Phase D landing + 2.0.0 release |
| 6+ | `main` ↔ `develop` reconciliation; Phase E (production hardening) |

## Blockers

1. **Main behind develop** — `main` is **11 commits behind** `develop`
   (down from 119 once Phase D landed). Decide: rebase merge, or
   fast-forward. The deprecation shim removal is no longer a
   precondition; the only remaining work is the actual merge. Track
   this as a standalone follow-up PR (candidate cut line for the
   2.0.1 patch release).
2. **Per-phase handlers in `application_submit`** — PR #166 (commit
   `d36c840`, Refs #145) extracted 5 per-phase handlers
   (search/score/cover-letter/skip/email/captcha) from
   `apply_to_vacancies.py` into `ApplicationSubmitSlice` handlers. The
   bridge is mostly done; the remaining work is small and tracked
   under issue #145.

### Resolved blockers

- ~~**Phase D entry-point decision**~~ — RESOLVED. PR #170 switched
  `[project.scripts]` to `job_bot.cli.main:main`; PR #182 and PR #192
  dropped `hh_applicant_tool` from the wheel; v2.0.0 ships without
  the legacy package.

## Known issues

- 2 pre-existing test failures in
  `tests/vsa/test_application_submit_handlers_captcha.py::TestCaptchaHandlerLegacyFallback`
  (the legacy Playwright fallback path; surfaced by a fixture
  condition after the PR #166 refactor). Being addressed in a
  separate PR by a follow-up subagent in parallel — **not** in scope
  for the roadmap doc PR.
- Pre-existing `ty` diagnostics in
  `src/job_bot/telegram_bot/services/review_service.py` (unrelated
  to the VSA migration; predates Phase D).
