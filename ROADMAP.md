# Project Roadmap

> Last updated: 2026-06-13 — see [issues](https://github.com/q-user/hh_apply/issues)
> for the authoritative task list. This document is a high-level summary,
> not a substitute for the issue tracker.

## Current State

**VSA foundation is in place**, but the migration is **not** complete.

- 7 VSA slices exist under `src/job_bot/` (config_auth, vacancy_search,
  channel_monitoring, max_bot, telegram_bot, application_prep,
  application_submit)
- 4 of those slices are "clean VSA" (no legacy imports)
- 3 are "mixed" (telegram_bot, application_prep, application_submit —
  VSA structure but services import from `hh_applicant_tool.*`)
- ~3100 LOC of old `hh_applicant_tool/` use-case code is not yet bridged
  to VSA
- Legacy `hh_applicant_tool/` is still the live entry point
  (`pyproject.toml` `packages`, `main.py`, `container.py`)
- `main` is 82 commits behind `develop`; no open PR bridging them
- CI on `develop` is currently broken: 62 ruff errors + 1 pytest
  failure (issue #82) — this blocks every other PR

## Phase A: Unblock CI (next 1-2 days)

| Issue | Task | Effort |
|-------|------|--------|
| #82 | Fix ruff errors + MagicMockDigest pytest failure on develop | M |

This is the **blocker for everything else**. Until CI is green, no
other VSA work can land cleanly.

## Phase B: Complete VSA switchover (next 1-2 weeks)

Wire the remaining slice(s) and deprecate the corresponding legacy code.

| Issue | Task | Effort | Notes |
|-------|------|--------|-------|
| #59 | Wire Config/Auth slice + deprecate old code | M | Last unwired switchover slice |
| #77 | VSA switchover (port scaffolding, shim contracts) | L | Work partly uncommitted — see `backup/vsa-77-switchover-2026-06-13` branch |
| #76 | Remove deprecated telegram/channel/MAX service code | M | Follow-up to PR #84 |
| New A | Bridge review_flow.py (1010 LOC) → VSA | L | — |
| New B | Bridge vacancy_tests.py (294 LOC) → VSA | M | — |
| New C | Bridge apply_to_vacancies.py (1117 LOC) → VSA | XL | Orchestrator |
| New D | Bridge prepare_vacancies.py (685 LOC) → VSA | L | Orchestrator |

**Success criteria:** all 7 VSA slices are clean (zero legacy imports),
4 old use-case files are bridged, `hh_applicant_tool` is reduced to a
thin deprecation shim package.

## Phase C: Standardize and clean (next 1 week)

| Issue | Task | Effort |
|-------|------|--------|
| New E | Standardize deprecation contract | S |
| New F | Move utilities to `job_bot/shared/` | M |
| New G | Fix VSA test isolation | S |
| New H | Delete dead code, wire/remove `Settings` | S |
| #63 | Integration tests (work landed via `70b5daf`, verify) | — |
| #83 | Replace mypy with ty | M |
| New I | Update documentation | M |

## Phase D: Production hardening (later)

- Observability: OpenTelemetry, structured logs, Prometheus metrics
- Health endpoints: `/health`, `/ready`
- Rate limiting per HH API endpoint
- Secrets management
- Sync `main` with `develop` (currently 82 commits behind)
- CI/CD improvements

## Timeline (rough estimate)

| Week | Focus |
|------|-------|
| 1 | Phase A (unblock CI) |
| 2-3 | Phase B (VSA switchover completion) |
| 4 | Phase C (standardize + clean + docs) |
| 5+ | Phase D (production hardening) |

## Blockers

1. **#82** — broken CI blocks every PR. Must be fixed first.
2. **Main behind develop** — `main` is 82 commits behind, no PR bridging
   them. Decide: rebase merge, or fast-forward once #82 lands.
