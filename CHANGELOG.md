# Changelog

All notable changes to `hh-applicant-tool` are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 2.0.0 — 2026-06-16

**SemVer impact: major.** The Vertical Slice Architecture migration has reached
the cut line called out in [issues](https://github.com/q-user/hh_apply/issues) (Phase D) and
`docs/vsa_migration_guide.md` (Phase 4): the legacy `hh_applicant_tool`
package is being retired in favour of the VSA-native `job_bot` package.

### Breaking changes

- **Removed the `hh_applicant_tool` distribution package.**
  A 5-LOC stub remains in place solely to emit a `DeprecationWarning` for
  any external code that still imports from it; the stub has no runtime
  surface. Migration target: import from `job_bot.*` instead.
- **Removed all legacy deprecation shims.** Every module under
  `src/hh_applicant_tool/{services,utils,operations,application,api,ai,storage,ui,telegram,infrastructure}/`
  that previously re-exported the VSA public API and emitted a
  standardised `DeprecationWarning` (issue #92, enforced by
  `tests/test_issue_92_deprecation.py`) is now gone. The VSA slices are
  the only supported import path.
- **`AppContainer` moved from `hh_applicant_tool.container` to
  `job_bot.container`.** The composition root that lazily wires the 7
  VSA slices (`application_prep`, `application_submit`, `channel_monitoring`,
  `config_auth`, `max_bot`, `telegram_bot`, `vacancy_search`) now lives
  in the VSA package. Update imports accordingly.
- **CLI entry point changed.** The `hh-applicant-tool` script now points
  to `job_bot.cli.main:main` (was `hh_applicant_tool.main:main`). The
  user-facing command name is unchanged; the dispatch table is the static
  `BUILTIN_OPERATIONS` registry in `job_bot.cli._base` (issue #149,
  PR #165).
- **Optional infrastructure extras unchanged.** The `playwright`,
  `openai` (via the `ai` extra), `pillow`, and `pywebview` (via the `ui`
  extra) optional dependency groups keep their existing names and
  version constraints. No action required for users who already
  installed them.

### Migration

- Replace `from hh_applicant_tool import …` with `from job_bot import …`.
- Replace `from hh_applicant_tool.container import AppContainer` with
  `from job_bot.container import AppContainer`.
- The CLI (`hh-applicant-tool …`) keeps working without changes; only
  programmatic importers need to update.
- See `docs/vsa_migration_guide.md` for the full slice map and port
  table.
