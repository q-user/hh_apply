# VSA Finalisation Plan — `hh_applicant_tool` Retirement

> **Status:** design document (no code changes)
> **Author:** design pass on 2026-06-15
> **Target repo:** `q-user/hh_apply`
> **Working branch for this plan:** `develop` (PR against `develop`, reconcile with `main` at cut line)
> **Related docs:** [`ROADMAP.md`](../ROADMAP.md), [`docs/vsa_migration_guide.md`](../docs/vsa_migration_guide.md), `tests/test_issue_92_deprecation.py`

---

## 1. Executive summary

The VSA migration's **switchover** is complete: 7 slices are wired through `AppContainer` in `src/hh_applicant_tool/container.py`, the bridge PRs (#129–#134) lifted the four heavy use cases and two Telegram-bot services into VSA, and the shim contract is enforced by `tests/test_issue_92_deprecation.py`. The remaining work in `src/hh_applicant_tool/` is **not** "more bridging" — it is **deletion** plus a small set of focused migrations that have nothing in `job_bot/` to bridge to yet:

1. The **shared kernel** has dead code: `BaseRepository` (ABC), `AIClient`, and `StorageFacade` (`shared/storage/facade.py`) with all repo attributes commented out. None of the VSA slice repositories extend the VSA `BaseRepository` in practice — they all use the *legacy* `BaseRepository` (dataclass) imported from `hh_applicant_tool.storage.repositories.base`. The VSA `BaseRepository` is imported by 6 repositories but its abstract methods are reimplemented inline, making the ABC ceremony dead weight.
2. **14 CLI ops** in `hh_applicant_tool/operations/` (~1960 LOC) are not yet migrated: 5 are deprecation shims (#137), 6 dispatch into VSA but live in the legacy package, and 13 are tiny utilities (`call_api`, `whoami`, `list_resumes`, `install`, `uninstall`, `migrate_db`, etc.) that need a new `job_bot.cli` home.
3. The **webview UI** (`ui/api.py` 673 LOC) is the largest unmigrated surface and the only piece still coupled to `HHApplicantTool` directly.
4. The **two heavy use cases** (`apply_to_vacancies` 1344 LOC, `prepare_vacancies` 989 LOC) own ~800 LOC of phase-level orchestration each (search, relevance, cover letter, filter, email, captcha, storage I/O) that the bridge PRs deliberately left inline.
5. The **entry point** (`main.py` 613 LOC, `container.py` 1151 LOC) is a `pkgutil.iter_modules` walker over `operations/*` plus a service-locator facade. The CLI dispatch and the facade must move to a VSA-native `__main__` and a slimmed-down `AppContainer`.

This plan proposes **16 new GitHub issues** (#143–#158), grouped into **7 phases**, against a **single new milestone** `VSA Finalisation` (#7) that supersedes the stale `VSA Migration` (#6) for the remaining work. Critical path: shared-kernel stabilisation → use-case phase splitting → CLI + UI migration → entry-point switchover → `hh_applicant_tool/` deletion → `main`/`develop` reconciliation.

The work totals **~16 PRs**, of which **5 are large** (the use-case phase splits and the UI migration), **8 are medium**, and **3 are small**. Estimated duration: 5–6 weeks of focused agent work. The `develop` branch stays mergeable at every step; the legacy package is reduced to a 5-line stub at the end of Phase 6 and deleted in Phase 7.

**Top risks:**
- The `AppContainer` composition root is a 1151-LOC god class. Touching it carelessly ripples to every slice.
- The UI's 673-LOC `api.py` is a `js_api` bridge that depends on `HHApplicantTool` directly; naively injecting a port would lose the `_ProgressHandler` / `_send_progress` / `_send_auth_event` callbacks that the webview JS relies on.
- The `pyproject.toml` entry-point name `hh-applicant-tool` is in widespread use; renaming it is a breaking change.
- Phase 7 (reconciliation) is irreversible once `main` and `develop` merge — a 119-commit gap must be closed cleanly.

---

## 2. Proposed GitHub issues

> **Conventions** (from existing PRs #129–#134, #122–#127, and `tests/test_issue_92_deprecation.py`):
> - Title: `refactor(vsa): <description> (Refs/Closes/fixes #N)`
> - Body: `## Summary`, `## What changed`, `## What did NOT change`, `## How verified` (with `uv run pytest` / `uv run ruff` / `uv run ty` output), `## SemVer impact`, `## Migration notes`, `## Followups`
> - Labels: `vsa`, `refactoring`, `tech-debt` (and `ui` for #149, `infra` for #151, `breaking` for #153/#155)
> - Milestone: `VSA Finalisation` (#7, **new** — see §3)
> - Size: **S** ≤300 LOC, **M** ≤1000 LOC, **L** >1000 LOC

### Issue #143 — **refactor(vsa): make VSA `BaseRepository` (ABC) the canonical base**  (M, blocks #145, #146)

> **Target slice:** `job_bot.shared.storage.repository`
> **Key files:** `src/job_bot/shared/storage/repository.py`, `src/job_bot/shared/storage/facade.py`, `src/job_bot/vacancy_search/repositories/vacancy_repo.py`, `src/job_bot/application_prep/repositories/application_repo.py`, + 4 other repos that import the VSA ABC

**Body.** Today the VSA `BaseRepository` is an `ABC[Generic[T]]` with abstract `create/get_by_id/update/delete` and three `_execute*` helpers. Six VSA slice repositories inherit from it but each one re-implements the abstract CRUD methods inline and re-creates its own connection-handling. The runtime base is the *legacy* `BaseRepository` (a `@dataclass` in `src/hh_applicant_tool/storage/repositories/base.py`); the VSA ABC is dead ceremony.

**Approach.** Promote the VSA `BaseRepository` to the canonical base by:
1. Adding concrete defaults for the abstract methods that derive SQL from the model class' table name (read from a `__table__` classvar on the model, mirroring the legacy contract).
2. Renaming it to `BaseSqliteRepository` and removing the `ABC` requirement — make `create/get_by_id/update/delete` concrete with sensible "table not configured" `NotImplementedError` defaults. This lets the VSA repos that *want* to override them still do so, while the four that don't need a full override (just `_init_table` + a couple of slice-specific queries) get a 60% LOC reduction.
3. Adding a `BaseRepository(Protocol)` *port* next to it (in `shared/storage/ports.py`) so cross-slice consumers depend on the protocol, not the implementation.
4. Re-exporting it as `job_bot.shared.storage.repository.BaseRepository` with a `DeprecationWarning` re-export of the old name for one release.

**Acceptance criteria**
- `src/job_bot/shared/storage/repository.py` is no longer an `ABC`; methods have concrete defaults.
- All 6 VSA repositories still pass their existing tests.
- `ruff check src/` and `uv run ty check src/job_bot/` clean.
- `tests/vsa/test_storage_base_repository.py` covers the new defaults (save/get/list/delete with a 3-line model).

**Dependencies:** none.
**Size:** M (~600 LOC across 9 files).

---

### Issue #144 — **refactor(vsa): fill out `shared/storage/facade.py` with all 14 repository properties** (S)

> **Target slice:** `job_bot.shared.storage`
> **Key files:** `src/job_bot/shared/storage/facade.py`, `src/job_bot/shared/storage/ports.py`

**Body.** `src/job_bot/shared/storage/facade.py:StorageFacade` is a `@dataclass` with only a `database: Database` field; all 14 repo attributes are commented out. This forces the `AppContainer` to do the wiring in `_get_vacancy_search_slice()` / `_get_application_prep_slice()` and prevents slices from sharing a `StorageFacade` instance. Fill out the facade with the 14 repositories (the same set the legacy `StorageFacade` exposes: application_drafts, application_test_answers, apply_jobs, employer_sites, employers, negotiations, resumes, search_profiles, settings, skipped_vacancies, telegram_sessions, vacancies, vacancy_contacts, **plus** the new relevance/cover_letter repos that live in `application_prep/repositories/`).

**Acceptance criteria**
- `StorageFacade` exposes all 14 repos; instantiating one is a one-liner: `StorageFacade.from_db_path("data.sqlite")`.
- The `StoragePort` Protocol in `shared/storage/ports.py` is updated to declare the 14 properties (not just `negotiations` / `skipped_vacancies` / `application_drafts`).
- New test `tests/vsa/test_shared_storage_facade.py` covers the factory and lazy init.
- The 4 shim modules that import from `hh_applicant_tool.storage` still work (the legacy `StorageFacade` is kept as a thin re-export for one more release; removed in #158).

**Dependencies:** #143.
**Size:** S (~250 LOC).

---

### Issue #145 — **refactor(vsa): port `application/use_cases/prepare_vacancies` per-phase handlers to VSA** (L, blocks #146)

> **Target slice:** `job_bot.application_prep`
> **Key files:** `src/hh_applicant_tool/application/use_cases/prepare_vacancies.py` (989 LOC), `src/job_bot/application_prep/handlers/` (3 handlers), `src/job_bot/application_prep/services/` (new)

**Body.** Issue #90 (PR #130) bridged `PrepareVacanciesUseCase` to `ApplicationPrepSlice.run_prepare_pipeline` but left the 989-LOC use case owning: profile loading, vacancy iteration, AI relevance filtering, cover letter generation, save-to-storage, the 3 `_build_*_handler` factories, and the `_save_vsa_draft_to_legacy_storage` reconciliation shim. Each is a candidate for a dedicated VSA service.

**Approach.** Split into 4 new files under `job_bot/application_prep/services/`:
- `services/profile_loader.py` (`ProfileLoaderService`) — wraps `_load_profiles` + `_fetch_published_resumes`.
- `services/vacancy_iteration.py` (`VacancyIterationService`) — wraps `_vacancy_search_loop` + `_process_vacancy`'s pure orchestration.
- `services/ai_filter.py` (`AiFilterService`) — wraps `_init_ai_filter` + relevance factory; pure (no DB).
- `services/draft_persister.py` (`DraftPersisterService`) — wraps `_save_vacancy_to_storage` + `_save_skipped_ai_rejected` + the `_save_vsa_draft_to_legacy_storage` shim.

The use case is reduced to a thin orchestrator (~250 LOC) that calls these services via constructor DI. Each new service is testable in isolation with a real DB. The `_build_*_handler` factory methods are deleted; the slice's `__init__` accepts the pre-built handlers.

**Acceptance criteria**
- `prepare_vacancies.py` is ≤300 LOC (a thin orchestrator).
- 4 new services live under `src/job_bot/application_prep/services/`, each with its own test file.
- `tests/test_prepare_vacancies.py` still passes (the legacy use case is now an adapter over the new services).
- The `ApplicationPrepSlice.run_prepare_pipeline` signature is unchanged.

**Dependencies:** #143, #144.
**Size:** L (~1200 LOC across 6 new files + 1 slimmed file).

---

### Issue #146 — **refactor(vsa): port `application/use_cases/apply_to_vacancies` per-phase handlers to VSA** (L, blocks #150, #155)

> **Target slice:** `job_bot.application_submit`
> **Key files:** `src/hh_applicant_tool/application/use_cases/apply_to_vacancies.py` (1344 LOC), `src/job_bot/application_submit/handlers/` (currently 4: apply_one, job, retry, test), `src/job_bot/application_submit/services/` (currently only `worker_service.py`)

**Body.** Issue #89 (PR #129) bridged `ApplyToVacanciesUseCase` to `ApplicationSubmitSlice.run_apply_pipeline` but the use case still owns: resume fetch, search-params building, vacancy iteration, AI relevance filter, cover letter generation, skip policy, employer storage, captcha solving, email sending. Each is a candidate for a dedicated handler.

**Approach.** Per the design decision in §4.4, extract **one handler per phase** into `src/job_bot/application_submit/handlers/`:
- `handlers/search_handler.py` — `_get_vacancies` + `_build_search_params` (uses `vacancy_search` slice's `build_search_params`).
- `handlers/score_handler.py` — AI relevance filter (delegates to `application_prep` slice's `RelevanceHandler`).
- `handlers/cover_letter_handler.py` — re-export of `application_prep.CoverLetterHandler` adapted to the submit phase.
- `handlers/skip_handler.py` — `_check_vacancy_skips` + blacklist filter.
- `handlers/email_handler.py` — `_send_email` + `_maybe_send_email` (uses `EmailSenderPort`).
- `handlers/captcha_handler.py` — `_solve_captcha_async`.
- The existing `apply_one_handler.py`, `test_handler.py`, `job_handler.py`, `retry_handler.py` stay.

The use case is reduced to a ~400-LOC orchestrator that wires the handlers via constructor DI. The `ApplicationSubmitSlice.run_apply_pipeline` is updated to call the in-slice handlers directly instead of going through the legacy use case, eliminating the `LegacyUseCasePort` indirection.

**Acceptance criteria**
- `apply_to_vacancies.py` is ≤500 LOC.
- 5 new handlers in `application_submit/handlers/`, each with tests.
- The `LegacyUseCasePort` Protocol in `application_submit/slice.py` is removed.
- `tests/test_prepare_vacancies.py` (which exercises the apply pipeline indirectly) and `tests/integration/test_telegram_channel_to_apply_flow.py` pass.

**Dependencies:** #143, #144, #145.
**Size:** L (~1500 LOC across 6 new files + 1 slimmed file).

---

### Issue #147 — **feat(cli): introduce `job_bot.cli` package with the 13 un-migrated operations** (M)

> **Target slice:** `job_bot.cli` (new top-level slice)
> **Key files:** new `src/job_bot/cli/` with 13 sub-commands: `call_api.py`, `check_proxy.py`, `clear_skipped.py`, `config.py`, `install.py`, `list_resumes.py`, `log.py`, `logout.py`, `migrate_db.py`, `refresh_token.py`, `settings.py`, `test_session.py`, `uninstall.py`, `update_resumes.py`, `whoami.py`

**Body.** The 13 small CLI ops listed in the user prompt (610 LOC total) have no VSA home. Most are 1-screen thin wrappers over the HH API or over the local storage. They are currently auto-registered by `main._create_parser` via `pkgutil.iter_modules(operations/)`.

**Approach.** Create `src/job_bot/cli/` with the same `Operation` class shape as the existing 6 VSA CLI ops (`apply_vacancies`, `apply_worker`, `channel_monitor`, `max_bot`, `telegram_bot`, `prepare_vacancies`). Each sub-command becomes a self-contained class that takes its dependencies (the VSA slice it needs) via constructor injection — no more `tool: HHApplicantTool` argument. Concretely:
- `cli/call_api.py` → uses `config_auth.ConfigAuthSlice` (OAuth + raw API).
- `cli/check_proxy.py` → uses `config_auth` for proxy config + a `requests.Session`.
- `cli/clear_skipped.py` → uses `shared.storage.facade.StorageFacade.skipped_vacancies`.
- `cli/config.py` → uses `config_auth.ConfigAuthSlice` (the existing `get_value/set_value/del_value` helpers move to a `config_auth.handlers.config_kv_handler.py`).
- `cli/install.py` / `cli/uninstall.py` → thin `subprocess` wrappers; no slice needed.
- `cli/list_resumes.py` → uses `vacancy_search` slice (the `get_resumes` port).
- `cli/log.py` → opens `LOG_FILENAME` from `shared.config.settings`.
- `cli/logout.py` → uses `config_auth` slice's `AuthPort`.
- `cli/migrate_db.py` → wraps `shared.storage.utils.apply_migration` / `list_migrations`.
- `cli/refresh_token.py` → uses `config_auth.AuthPort.refresh_access_token`.
- `cli/settings.py` → uses `shared.storage.facade.StorageFacade.settings`.
- `cli/test_session.py` → uses `config_auth` slice.
- `cli/update_resumes.py` → uses `vacancy_search` slice (publish endpoint).
- `cli/whoami.py` → uses `config_auth` slice's `UserPort`.

Each sub-command has a test under `tests/vsa/cli/` (or co-located with the slice it depends on if there's an obvious home).

**Acceptance criteria**
- `src/job_bot/cli/__init__.py` re-exports a `BUILTIN_OPERATIONS: list[type[Operation]]` registry.
- All 13 sub-commands pass `python -m job_bot <command> --help` round-trip.
- 13 new test files (or extension of existing VSA slice tests) pass.
- `ruff` + `ty` clean.

**Dependencies:** #143, #144.
**Size:** M (~900 LOC across 14 new files + 13 new test files).

---

### Issue #148 — **refactor(vsa): replace `pkgutil.iter_modules` CLI dispatch with a static registry** (S, blocks #155)

> **Target slice:** `job_bot.cli` (continuation of #147)
> **Key files:** `src/hh_applicant_tool/main.py` (`_create_parser` at L88-151), `src/job_bot/cli/registry.py` (new), `src/job_bot/cli/__init__.py`

**Body.** `main._create_parser` walks `operations/` with `pkgutil.iter_modules`, imports each module, instantiates `Operation()`, and registers a sub-parser. This couples the CLI to the *presence* of a module on disk and forces the legacy `main.py` to know about every VSA op. With #147, all ops live in `job_bot.cli` and can be enumerated statically.

**Approach.** Replace the `iter_modules` walk with a `BUILTIN_OPERATIONS: list[type[BaseOperation]]` registry exported from `job_bot.cli`. `main._create_parser` (or its VSA replacement in #155) iterates the registry. The `BaseOperation`/`BaseNamespace` classes move from `hh_applicant_tool.main` to `job_bot.cli._base` (with a deprecation re-export).

**Acceptance criteria**
- `main._create_parser` no longer calls `pkgutil.iter_modules`.
- Removing a sub-command from the registry is a one-line change.
- The `test_operations_*` tests (which exercise each op's `--help` parser) still pass.

**Dependencies:** #147.
**Size:** S (~150 LOC).

---

### Issue #149 — **refactor(vsa): decouple `ui/api.py` from `HHApplicantTool` via a `UiApiContext` port** (L, blocks #155)

> **Target slice:** `job_bot.ui` (new top-level slice)
> **Key files:** new `src/job_bot/ui/` with `__init__.py`, `api.py` (slimmed 673 → ~400 LOC), `ports.py` (UiApiContext Protocol), `presets.py` (move from `hh_applicant_tool.ui`), `templates/index.html` + `js/app.js` + CSS, `slice.py` (UiSlice factory)

**Body.** `hh_applicant_tool/ui/api.py` is the largest unmigrated file. It defines `class Api`, a pywebview `js_api` bridge with ~30 methods, all reading/writing via `self._tool: HHApplicantTool` directly. The `_ProgressHandler` and `_send_progress` / `_send_auth_event` callbacks are pywebview-specific (they call into the webview window's JavaScript).

**Approach.** Per the design decision in §4.3, introduce a `UiApiContext` `@dataclass` that bundles the dependencies `Api` actually uses (no method reaches deeper than `tool.X.Y`):
- `api_client: HhApiClientPort` (raw HTTP wrapper)
- `config: ConfigPort` (KV via `config_auth`)
- `storage: StoragePort` (the 14-repo facade from #144)
- `apply_use_case: ApplyToVacanciesUseCase` (still legacy, but the slice depends on it via a `LegacyUseCasePort` Protocol — same trick as `ApplicationSubmitSlice`)
- `prepare_use_case: PrepareVacanciesUseCase` (same)
- `presets: PresetsManager` (moves from `hh_applicant_tool.ui.presets`; uses `SettingsRepository` from `config_auth`)
- `progress_sink: Callable[[int, int, str], None]` (the `_send_progress` callback)
- `auth_event_sink: Callable[[str, str], None]` (the `_send_auth_event` callback)
- `window: Any` (set by `UiSlice.set_window()`)

The `Api` class is rewritten to take a `UiApiContext`. Each of the 30 methods becomes a 1-3 line dispatch into the right port. The `presets.py` module moves wholesale to `src/job_bot/ui/presets.py` and uses the new `StoragePort` for persistence. The `templates/` directory moves to `src/job_bot/ui/templates/` (path referenced by `pyproject.toml` `include = [...]`).

A new `UiSlice` class (in `src/job_bot/ui/slice.py`) wires the context together: it takes the same dependencies `AppContainer` already builds (`api_client`, `config_slice`, `application_submit_slice`, `application_prep_slice`, `storage`) and constructs a `UiApiContext`. `create_window(tool, *, debug)` becomes `create_window(ui_slice, *, debug)`.

**Acceptance criteria**
- `src/job_bot/ui/api.py` is ≤500 LOC and no longer imports `HHApplicantTool`.
- `src/job_bot/ui/presets.py` no longer imports `hh_applicant_tool.storage`.
- The webview HTML/JS round-trip works (the `js/api.js` calls are unchanged because `Api` exposes the same methods).
- `tests/vsa/test_ui_slice.py` covers the new `UiApiContext` wiring.

**Dependencies:** #143, #144, #145, #146.
**Size:** L (~1400 LOC across 5 new files + 1 slimmed file + 1 directory move).

---

### Issue #150 — **refactor(vsa): port `utils/{cookiejar,mixins,resume_md,terminal}` to `shared/utils` and `application_prep/services/`** (M)

> **Target slice:** `job_bot.shared.utils` (cookiejar, mixins, terminal); `job_bot.application_prep.services` (resume_md)
> **Key files:** `src/hh_applicant_tool/utils/cookiejar.py` (23), `mixins.py` (68), `terminal.py` (134), `resume_md.py` (611)

**Body.** Per issue #93, cross-cutting utilities were moved to `shared/utils` but four modules remained because no VSA slice depended on them:
- `cookiejar.py` (`HHOnlyCookieJar`) — only used by `main.py`'s `_create_http_session` and `MegaTool` mixin. It is HTTP-layer logic; belongs in `shared/http` (new) or `shared.utils.cookiejar`.
- `mixins.py` (`MegaTool`) — the service-locator mixin. Once #155 lands, `HHApplicantTool` is gone, so this mixin is dead. Delete it.
- `terminal.py` (`setup_terminal`) — platform detection; pure stdlib. Belongs in `shared/utils/terminal.py`.
- `resume_md.py` (611 LOC) — Markdown resume builder. The only consumer is `operations/create_resume` (#137 shim) which lives in the new `job_bot.resume_management` slice. Move to `job_bot.resume_management.services.resume_renderer.py`.

**Acceptance criteria**
- `src/hh_applicant_tool/utils/` is empty (or contains only a stub `__init__.py` with the deprecation re-exports from #93).
- `MegaTool` is deleted; no live callers.
- `resume_md.py` lives in `job_bot.resume_management.services` and is consumed by `ResumeCreateHandler` (already in #137).
- All existing tests pass.

**Dependencies:** #147 (the new `cli/create_resume.py` consumes the relocated module).
**Size:** M (~900 LOC moved + 1 deletion).

---

### Issue #151 — **refactor(vsa): port `infrastructure/*` to `shared/infrastructure/` (or per-slice `services/`)** (M)

> **Target slice:** `job_bot.shared.infrastructure` (new) and per-slice `services/`
> **Key files:** `src/hh_applicant_tool/infrastructure/ai.py` (161), `captcha.py` (207), `delay.py` (133), `email.py` (104), `http.py` (209), `test_logger.py` (126), `time.py` (98), `vacancy_fetcher.py` (130)

**Body.** The 8 `infrastructure/*` modules are Concrete implementations of the `Protocol` ports declared in `application/ports.py`. The use case bridges (#129, #130) already accept these via constructor injection, so they are runtime-active code; they are *not* shims. They belong in the VSA world, not in `hh_applicant_tool`.

**Approach.** Move each module to its natural home:
- `ai.py` (AI config + builder) → `shared/ai/builder.py` (the `AIClient` in `shared/ai/client.py` is the abstract; the `infrastructure/ai.py` is the concrete factory).
- `captcha.py` → `application_submit/services/captcha_solver.py` (only the apply pipeline uses captcha).
- `delay.py` → `shared/utils/delay.py` (pure stdlib).
- `email.py` (SMTP) → `application_submit/services/email_sender.py` (only the apply pipeline sends email).
- `http.py` (`RequestsSiteParser`) → `shared/http/site_parser.py`.
- `test_logger.py` (`FileTestVacancyLogger`) → `application_submit/services/test_logger.py`.
- `time.py` (clock) → `shared/utils/clock.py`.
- `vacancy_fetcher.py` (full-vacancy GET) → `vacancy_search/services/vacancy_fetcher.py` (the `vacancy_search` slice already has a `VacancyHandler`; this is a concrete implementation of full-vacancy fetch).

**Acceptance criteria**
- `src/hh_applicant_tool/infrastructure/` is empty.
- The `ApplicationSubmitAdapter` / `ApplicationPrepAdapter` in `container.py` import from the new locations.
- All existing tests pass.
- `ty` clean on `src/job_bot/`.

**Dependencies:** #143, #144, #145, #146.
**Size:** M (~1200 LOC moved + 1 deletion of `infrastructure/`).

---

### Issue #152 — **refactor(vsa): port `api/datatypes.py` and `api/errors.py` to `shared/api/`** (S)

> **Target slice:** `job_bot.shared.api` and `job_bot.application_submit.errors`
> **Key files:** `src/hh_applicant_tool/api/datatypes.py` (294), `src/hh_applicant_tool/api/errors.py` (148)

**Body.** `api/datatypes.py` is a bag of `TypedDict`s for HH API responses (`User`, `Vacancy`, `Resume`, `SearchVacancy`, `PaginatedItems`, etc.). `api/errors.py` defines `ApiError` + 8 subclasses (`CaptchaRequired`, `LimitExceeded`, `Forbidden`, etc.).

**Approach.** Move verbatim:
- `api/datatypes.py` → `shared/api/datatypes.py`. Each TypedDict gets a small `to_internal()` adapter (re-exported under the original name to preserve the dotted-path call sites).
- `api/errors.py` → `shared/api/errors.py`. The `ApiError` exception class is shared; the slice-specific ones (`CaptchaRequired`, `LimitExceeded`) move to `application_submit/errors.py` (where `RetryableError` / `FatalError` already live).

**Acceptance criteria**
- `src/hh_applicant_tool/api/` is empty.
- The 1 active caller (`hh_applicant_tool/api/__init__.py` re-exports) is updated.
- All existing tests pass.

**Dependencies:** none (this is independent of the storage work).
**Size:** S (~450 LOC moved + 1 directory deletion).

---

### Issue #153 — **feat(vsa): add VSA-native `__main__.py` and switch `[project.scripts]` entry point** (S, **breaking**, blocks #155)

> **Target slice:** `job_bot.__main__`
> **Key files:** new `src/job_bot/__main__.py`, `src/job_bot/cli/main.py` (the new entry point), `pyproject.toml` `[project.scripts]`

**Body.** Today `pyproject.toml` declares `hh-applicant-tool = "hh_applicant_tool.main:main"`. With the legacy `main.py` gone (#155), the entry point must move.

**Approach.** Introduce `src/job_bot/__main__.py` (5 LOC: `from job_bot.cli.main import main; sys.exit(main())`) and `src/job_bot/cli/main.py` (~100 LOC) that:
1. Constructs `AppContainer` (now slimmed in #154).
2. Calls `AppContainer.run(argv)`.
3. The body of `run()` is the same `try/except` block from the old `HHApplicantTool.run()`.

`pyproject.toml` `[project.scripts]` becomes:
```toml
[project.scripts]
hh-applicant-tool = "job_bot.cli.main:main"
```

The old entry-point name `hh-applicant-tool` is **preserved** to avoid breaking user shell aliases, CI scripts, and Docker `CMD`. The change is observable only as a `python -c 'import hh_applicant_tool'` *deprecation* in #155, not a script-name change.

**Acceptance criteria**
- `uv run hh-applicant-tool --help` and `python -m job_bot --help` produce the same output.
- Old `python -m hh_applicant_tool` still works (delegated via the #155 stub).
- `pyproject.toml` updated.
- `[tool.poetry.scripts]` (legacy) updated identically.

**Dependencies:** #148 (the static registry must exist).
**Size:** S (~150 LOC + 1 config edit).

---

### Issue #154 — **refactor(vsa): slim `AppContainer` to a pure VSA composition root** (M, blocks #155)

> **Target slice:** `job_bot.container` (new top-level module — `src/job_bot/container.py`)
> **Key files:** `src/hh_applicant_tool/container.py` (1151 LOC) → `src/job_bot/container.py` (~300 LOC); the 4 legacy `_VacancySearchAdapter` / `_ApplicationPrepAdapter` / `_ApplicationSubmitAdapter` / `_ConfigAdapter` go away.

**Body.** `AppContainer` is a 1151-LOC lazy-singleton factory for 7 VSA slices + 4 adapters. The adapters are the legacy bridge pattern: they re-shape VSA slices back into the dict-like / method-like surface the legacy use cases expect. With #145 and #146 done, the use cases shrink enough that the adapters can be deleted.

**Approach.** `AppContainer` becomes a thin factory:
- 7 `@cached_property` accessors for the 7 VSA slices (no lazy-instantiation tricks needed; the slices are cheap to build).
- 1 method `run(argv)` that constructs a `BaseNamespace` and dispatches via the static registry from #148.
- 1 method `apply_to_vacancies_use_case(...)` that returns the use case, wired with the slimmed `apply_to_vacancies.py` from #146.
- 1 method `prepare_vacancies_use_case(...)` that returns the use case, wired with the slimmed `prepare_vacancies.py` from #145.
- The 4 `_Adapter` classes are deleted; the legacy use cases depend on the VSA slices directly via constructor injection (the use cases *already* do this — the adapters exist only to flatten the call from `main.py`).

**Acceptance criteria**
- `src/job_bot/container.py` is ≤400 LOC.
- The 4 `_Adapter` classes are deleted.
- `AppContainer` imports zero symbols from `hh_applicant_tool`.
- All existing tests pass.

**Dependencies:** #145, #146.
**Size:** M (~800 LOC removed + ~100 LOC new).

---

### Issue #155 — **refactor(vsa): delete `hh_applicant_tool` package, leave a 5-line stub** (L, **breaking**, blocks #156, #157, #158)

> **Target slice:** all (final deletion)
> **Key files:** `src/hh_applicant_tool/` is reduced to `__init__.py` (5 LOC) and `__main__.py` (5 LOC) that re-export from `job_bot`.

**Body.** Once #143–#154 are merged, the only remaining code in `hh_applicant_tool/` is:
- `__init__.py` (1 LOC: `from .main import HHApplicantTool`)
- `__main__.py` (5 LOC: `from .main import main; sys.exit(main())`)
- `constants.py` (15 LOC: path constants)
- 5 deprecation shim modules listed in `tests/test_issue_92_deprecation.py:SHIM_CONTRACT`
- `utils/__init__.py` (the module-level shim from #93)
- The legacy `storage/` (1155 LOC, still runtime-active for the `StorageFacade` migration in #144)
- The legacy `application/` (use cases + DTO + ports, all still in use)

**Approach.** This is a "delete everything, keep a stub" issue. After #158 (#144 wired, #150 + #151 + #152 cleaned up), nothing in `hh_applicant_tool/` is live code. The package becomes:
```python
# src/hh_applicant_tool/__init__.py
import warnings
warnings.warn(
    "hh_applicant_tool is deprecated; use job_bot instead. "
    "The package will be removed in 2.0.",
    DeprecationWarning, stacklevel=2,
)
__all__ = ["__version__"]
__version__ = "1.9.0"  # bumped
```

```python
# src/hh_applicant_tool/__main__.py
import warnings
warnings.warn(
    "hh_applicant_tool.__main__ is deprecated; use 'python -m job_bot' "
    "or the hh-applicant-tool script (which now points at job_bot).",
    DeprecationWarning, stacklevel=2,
)
from job_bot.__main__ import main
import sys
sys.exit(main())
```

All other files are deleted. `pyproject.toml` `packages = [{ include = "hh_applicant_tool", from = "src" }]` is kept for the stub (the package *exists*, it just contains a warning). The `[project.scripts]` from #153 points at `job_bot.cli.main:main`, not `hh_applicant_tool.main:main`. The 5-LOC stub is what the 3rd-party code that does `from hh_applicant_tool import X` will hit, getting a clear deprecation.

**Acceptance criteria**
- `src/hh_applicant_tool/` contains only `__init__.py` and `__main__.py`.
- `python -c 'import hh_applicant_tool'` emits a `DeprecationWarning` and works.
- `python -m hh_applicant_tool` still runs (delegates to `job_bot`).
- `python -m job_bot` and `hh-applicant-tool` (the script) both work.
- `ruff` + `ty` + `pytest` clean.

**Dependencies:** #143, #144, #145, #146, #147, #148, #149, #150, #151, #152, #153, #154.
**Size:** L (~6000 LOC deleted; +10 LOC stub added).

---

### Issue #156 — **chore: bump version to 2.0.0 (SemVer major)** (S, blocks #158)

> **Key files:** `pyproject.toml` (`version = "2.0.0"`), `CHANGELOG.md`, `src/job_bot/__init__.py` (`__version__ = "2.0.0"`)

**Body.** Removing the `hh_applicant_tool` package is a SemVer-major change. The 2.0.0 release is the natural cut line that the `ROADMAP.md` and `docs/vsa_migration_guide.md` both call out as the goal.

**Acceptance criteria**
- `pyproject.toml` `version = "2.0.0"`.
- `CHANGELOG.md` has a `## 2.0.0` section listing the breaking changes.
- `uv lock` regenerates cleanly.

**Dependencies:** #155.
**Size:** S (~30 LOC).

---

### Issue #157 — **chore: drop legacy tests under `tests/test_*.py` that exercise deleted shims** (S)

> **Key files:** `tests/test_issue_92_deprecation.py` (the `SHIM_CONTRACT` table is emptied), `tests/test_issue_57_deprecation.py`, `tests/test_issue_58_deprecation.py`, + several others that only test legacy shims

**Body.** A handful of test files exist *only* to enforce the deprecation contract on shim modules. Once #155 lands, those modules are gone, and the tests become tautological (they test `warnings.warn`).

**Approach.** Audit `tests/test_*.py` (the non-`vsa/` non-`integration/` directory) and remove files that:
1. Test only deprecated shim modules (e.g. `test_services_applications.py` if it exists).
2. Test legacy infrastructure that has been deleted (#150, #151, #152).

Keep the tests that exercise the *new* VSA code (moved under `tests/vsa/` or co-located).

**Acceptance criteria**
- `tests/test_issue_92_deprecation.py` exists but its `SHIM_CONTRACT` is empty.
- The 1014 currently-passing tests still pass; the removed ones are documented in the PR.
- The 7 xfailed tests are unchanged.

**Dependencies:** #155.
**Size:** S (~200 LOC removed).

---

### Issue #158 — **chore: reconcile `main` with `develop` (close 119-commit gap)** (S, **irreversible**)

> **Key files:** entire `main` branch, all files

**Body.** The `ROADMAP.md` notes that `main` is 119 commits behind `develop` (the natural cut line for the 2.0 release). Issue #137/#142/#155 + #156 make the 2.0.0 release viable.

**Approach.** Three options, in order of preference:
1. **Fast-forward `main` to `develop`**: works only if `main` is a strict ancestor. Likely the case (the gap is unmerged commits, not divergent work).
2. **Merge `develop` into `main` with a merge commit**: the standard GitHub-flow release pattern; preserves `main`'s history.
3. **Rebase `main` onto `develop`**: rewrites `main`'s history; not appropriate for a shared branch.

**Recommendation:** option 2 (merge commit) because `main` is shared and has the production `pyproject.toml` `version = "1.8.10"` tag. The merge commit message is "Release 2.0.0 — `hh_applicant_tool` retired". This is the only **irreversible** step in the plan.

**Acceptance criteria**
- `git log main..develop` is empty after the merge.
- The 2.0.0 tag is created on the merge commit.
- `git tag --list 'v2.*'` shows the new tag.
- A `## 2.0.0` section is in `CHANGELOG.md`.

**Dependencies:** #156.
**Size:** S (1 commit, 1 tag, 1 CHANGELOG entry).

---

## 3. Milestone recommendation

### Recommendation: create a new milestone **`VSA Finalisation`** (#7), keep #6 `VSA Migration` for historical context

**Rationale.** Milestone #6 `VSA Migration` has a misleading description. The "### Switchover" list marks #56–#58 as `TODO` but they are *closed*; the "### Already completed" list grows stale as each issue closes. Re-purposing it (option (a) in the brief) would require editing the description heavily and putting very different work in the same milestone — confusing for the next contributor.

A new milestone `VSA Finalisation` (#7) groups the 16 issues in §2 with a clean, forward-looking description:

> **Title:** VSA Finalisation
> **Description:** Complete the VSA migration by deleting `hh_applicant_tool`. Builds on the slices wired in milestone #6 (VSA Migration) and the bridge PRs #129–#134. Tracks the 16-issue plan in `docs/vsa_finalisation_plan.md`. The 2.0.0 release is the natural cut line.
>
> ### Critical path
> 1. Stabilise the shared kernel (#143, #144)
> 2. Per-phase handler extraction (#145, #146)
> 3. CLI + UI migration (#147, #148, #149, #150)
> 4. Infrastructure + shared-kernel housekeeping (#151, #152)
> 5. Entry-point switchover + composition root slimming (#153, #154)
> 6. Package deletion + version bump + test cleanup (#155, #156, #157)
> 7. `main` ↔ `develop` reconciliation (#158)
>
> ### Predecessors (closed in #6 VSA Migration)
> #50, #53–#59, #76, #77, #87–#90, #92–#96, #122–#127, #129–#134, #142

The 16 new issues are all assigned to milestone #7. The existing milestone #6 is left in place (it has 19 closed issues, no open ones) for traceability — anyone exploring the project's history can still see what was done in the switchover phase.

**Due date:** 2026-07-14 (matching #6's original date; the 2.0.0 release).

---

## 4. Phased execution roadmap

> The phases are the **execution order**, not the milestone structure. All 16 issues live in milestone #7.

### Phase 1 — Stabilise the shared kernel (issues #143, #144; ~1 week)

The slice repos that exist today inherit a dead ABC and call into a facade with no fields. Before we can extract per-phase handlers from the use cases (#145, #146), the storage foundation must be solid: one canonical `BaseRepository`, one `StorageFacade` exposing all 14 repos.

**Prerequisites:** none.
**Issues:** #143, #144.
**Validation gates:**
- `uv run ty check src/job_bot/` clean.
- `uv run pytest tests/vsa/test_storage_base_repository.py` passes.
- `uv run pytest tests/vsa/test_vacancy_search_slice.py tests/vsa/test_application_prep_relevance_vsa_path.py` still pass (the 6 affected repos).

### Phase 2 — Per-phase handler extraction from the heavy use cases (issues #145, #146; ~2 weeks)

The 1344-LOC `apply_to_vacancies` and 989-LOC `prepare_vacancies` use cases are the two biggest unmigrated surfaces. The bridge PRs (#129, #130) left ~800 LOC of inline phase logic in each. We extract one handler per phase into the appropriate slice. This is the highest-risk phase (touches 5+ test files, 10+ source files), and it unblocks the UI migration (#149) and the composition-root slimming (#154).

**Prerequisites:** Phase 1 (#143, #144 must be merged).
**Issues:** #145, #146.
**Validation gates:**
- `uv run pytest tests/test_prepare_vacancies.py` passes (1004 lines of test).
- `uv run pytest tests/test_apply_jobs.py` passes.
- `uv run pytest tests/integration/` passes.
- `apply_to_vacancies.py` ≤500 LOC, `prepare_vacancies.py` ≤300 LOC.

### Phase 3 — CLI + UI migration (issues #147, #148, #149, #150; ~1.5 weeks)

With Phase 2 done, the use cases are thin enough that the UI's `_ApplicationPrepAdapter` / `_ApplicationSubmitAdapter` shims can be deleted (or never created). The 13 small CLI ops get a VSA home in `job_bot.cli`. The 673-LOC `ui/api.py` is rewritten against a `UiApiContext` port. The 4 unmigrated `utils/*` modules move to their slice homes.

**Prerequisites:** Phase 2 (#145, #146 — the UI depends on the slim use cases).
**Issues:** #147, #148, #149, #150. Note #149 is the largest single PR; #147+#148 can ship first to unblock #153.

**Validation gates:**
- `uv run pytest tests/vsa/cli/` passes (13 new test files).
- `uv run pytest tests/vsa/test_ui_slice.py` passes.
- Manual webview test: `hh-applicant-tool ui` opens, the JS bridge round-trips a `get_resumes()` call.
- `MegaTool` is unused by `grep -r 'MegaTool' src/`.

### Phase 4 — Infrastructure + shared-kernel housekeeping (issues #151, #152; ~3 days)

`infrastructure/*` and `api/*` are runtime-active; they're not blocking, but they can't be deleted until #155, and moving them now keeps the diff in #155 small. Independent of Phases 2-3.

**Prerequisites:** none (can run in parallel with Phase 3 after #145 lands, because #149 imports from `infrastructure/email.py`).
**Issues:** #151, #152.

**Validation gates:**
- `grep -r 'from hh_applicant_tool.infrastructure' src/` returns nothing.
- `grep -r 'from hh_applicant_tool.api' src/` returns nothing.
- `uv run pytest` clean.

### Phase 5 — Entry-point switchover + composition root slimming (issues #153, #154; ~3 days)

With Phase 2 done, the use case adapters in `AppContainer` are redundant. With Phase 3 done, the CLI dispatch is a static registry. The entry point can switch to `job_bot.cli.main:main`.

**Prerequisites:** #148 (static registry), #145+#146 (slim use cases).
**Issues:** #153, #154.

**Validation gates:**
- `uv run hh-applicant-tool --help` works.
- `python -m job_bot --help` works.
- `AppContainer` ≤400 LOC.
- The 4 `_Adapter` classes are deleted.

### Phase 6 — Package deletion + version bump + test cleanup (issues #155, #156, #157; ~2 days)

The legacy package becomes a 5-LOC stub. The version is bumped to 2.0.0. The legacy-only test files are removed.

**Prerequisites:** all of Phases 1-5.
**Issues:** #155, #156, #157.

**Validation gates:**
- `src/hh_applicant_tool/` contains only `__init__.py` (5 LOC) and `__main__.py` (5 LOC).
- `python -c 'import hh_applicant_tool'` emits a `DeprecationWarning` and works.
- `uv run pytest` clean (1014 - removed tests, + 0 new tests).

### Phase 7 — `main` ↔ `develop` reconciliation (issue #158; ~1 day)

The 119-commit gap is closed. The 2.0.0 tag is cut. This is the only **irreversible** step.

**Prerequisites:** #156.
**Issues:** #158.

**Validation gates:**
- `git log main..develop` empty.
- `git tag --list 'v2.*'` shows `v2.0.0`.
- `CHANGELOG.md` has a `## 2.0.0` section.

### Critical-path summary

```
Phase 1 (#143 → #144)
       ↓
Phase 2 (#145 → #146)
       ↓                ↘  #151, #152 (parallel)
Phase 3 (#147 → #148 → #149, #150)
       ↓
Phase 5 (#153, #154)        (depends on #148 and #145/#146)
       ↓
Phase 6 (#155, #156, #157)
       ↓
Phase 7 (#158)
```

**Total estimated duration:** 5–6 weeks of focused agent work, with Phases 3 and 4 partially parallelizable.

---

## 5. Design decisions for the 5 tricky questions

### 5.1 Storage layer migration — dead `BaseRepository` (ABC) (issue #143)

**Question:** do we (a) port each repository to its own slice's `repositories/` (current pattern), or (b) fill out the VSA `shared/storage/facade.py` with all 14 repos?

**Answer:** **Both, in the right order.** Issue #143 makes the VSA `BaseRepository` (in `shared/storage/repository.py`) the canonical base — but it stays abstract enough to allow per-slice repos to add their slice-specific methods. Issue #144 then fills out the `StorageFacade` to expose all 14 repos. Slices still own their own repos (option (a)) for cohesion — but the facade is a thin aggregator (option (b)) so cross-slice consumers can depend on a single `StoragePort` instead of importing 14 sub-slice repos.

**Rationale.** The current pattern (per-slice `repositories/`) is good — each slice owns its data shape. The VSA `BaseRepository` was supposed to be the common base class, but it ended up as a dead ABC because:
1. The abstract methods are too narrow (`create/get_by_id/update/delete` with no hint on how to derive SQL from a model class).
2. The legacy `BaseRepository` (a `@dataclass` in `hh_applicant_tool/storage/repositories/base.py`) provides a much richer API (`find`, `save`, `save_batch`, `count_total`, `delete`, `clear`, with operator-based filtering `find(status__in=[...])`) that the VSA repos re-implement poorly.
3. The `StorageFacade` in `shared/storage/facade.py` is empty (all 14 attrs commented out), forcing every consumer to know which slice owns which repo.

**Concrete plan (#143).**
- Rename `BaseRepository` (in `shared/storage/repository.py`) to `BaseSqliteRepository` and make it a concrete class (not an `ABC`).
- Provide default implementations of `create/get_by_id/update/delete` that derive SQL from `model.__table__` (a classvar on the model, mirroring the legacy `__table__`).
- Add the legacy methods (`find`, `save`, `save_batch`, `count_total`, `delete`, `clear`) as concrete methods on `BaseSqliteRepository` — extracted from `hh_applicant_tool/storage/repositories/base.py` verbatim.
- Re-export `BaseRepository = BaseSqliteRepository` with a `DeprecationWarning` for one release.
- The 6 existing VSA repos (`vacancy_search/repositories/{search_profile_repo,vacancy_repo}.py`, `application_prep/repositories/{application_repo,cover_letter_repo,relevance_repo}.py`) lose ~50 LOC each as their re-implementations are replaced by the inherited defaults.

**Concrete plan (#144).**
- Fill out `StorageFacade` (in `shared/storage/facade.py`) with all 14 repos as `@property` accessors (lazy-constructed from a single `Database` instance).
- Add a `StoragePort` Protocol (in `shared/storage/ports.py`) declaring the 14 properties.
- The `AppContainer` is simplified: instead of constructing 14 repos in 7 slice factories, it constructs one `StorageFacade` and passes it to each slice's factory.

**What we explicitly do *not* do:**
- We do *not* delete the legacy `BaseRepository` in `hh_applicant_tool/storage/repositories/base.py` yet. The legacy 13 model classes still extend it, and the legacy `StorageFacade` still uses it. The deletion happens in #155 (when `hh_applicant_tool/storage/` is wiped).

### 5.2 CLI dispatch — replacing `pkgutil.iter_modules` (issue #148)

**Question:** (a) build a static registry in `job_bot.cli`, or (b) keep the `pkgutil.iter_modules` walk but against a new `job_bot.cli.operations` package?

**Answer:** **(a) Static registry.** `pkgutil.iter_modules` is the wrong abstraction here — it couples the CLI to the *physical* location of operations, and it forces every `Operation` class to be importable at parser-construction time (eager import, slow startup). A static registry is a one-liner that gives the same extensibility for 3rd-party plugins (`register_operation(MyOp())`) without the import-time cost.

**Concrete shape.**
```python
# src/job_bot/cli/__init__.py
from job_bot.cli._base import BaseOperation, BaseNamespace
from job_bot.cli.call_api import CallApiOperation
from job_bot.cli.whoami import WhoamiOperation
# ... 18 more imports

BUILTIN_OPERATIONS: tuple[type[BaseOperation], ...] = (
    CallApiOperation,
    WhoamiOperation,
    # ... 18 more classes
)

__all__ = ["BaseOperation", "BaseNamespace", "BUILTIN_OPERATIONS"]
```

The `main` function iterates `BUILTIN_OPERATIONS` and adds a sub-parser for each. A 3rd-party plugin can append to the tuple (or to a separate `EXTERNAL_OPERATIONS` list that `main` also iterates).

**Migration shape in #147.** The 13 un-migrated ops are written as VSA-style `Operation` classes from day one (each takes its slice dependencies via `__init__`). The 6 already-VSA ops (`apply_vacancies`, `apply_worker`, `channel_monitor`, `max_bot`, `telegram_bot`, `prepare_vacancies`) are *moved* from `hh_applicant_tool/operations/` to `job_bot/cli/` and re-typed as VSA classes (no more `Operation.run(self, tool: HHApplicantTool, args: Namespace)` — they take the slice they need via DI).

### 5.3 UI migration — decoupling `ui/api.py` from `HHApplicantTool` (issue #149)

**Question:** (a) a `UiApiFacade` port that the VSA `UiSlice` implements, or (b) `AppContainer` builds a `UiApiContext` dataclass and injects it?

**Answer:** **(b) `UiApiContext` dataclass.** A `UiApiFacade` Protocol would force `Api` to be a class implementing 30+ methods; a dataclass bundle of dependencies lets `Api` stay a class but consumes the dataclass as a simple namespace. The pywebview `js_api` protocol requires `Api` to be a class with public methods (the JS calls `window.pywebview.api.method_name(...)`), so a Protocol-as-`Api`-interface doesn't work — but a context-dataclass that `Api.__init__` unpacks is a clean middle ground.

**Concrete shape.**
```python
# src/job_bot/ui/ports.py
from dataclasses import dataclass
from typing import Any, Callable, Protocol

@dataclass
class UiApiContext:
    """Bundle of dependencies for the webview ``Api`` bridge.

    Constructed by :class:`UiSlice` from the VSA slices it owns; the
    legacy ``AppContainer``-shaped tool facade is not exposed here.
    """
    api_client: Any            # HhApiClientPort — raw HTTP wrapper
    config: Any                # ConfigPort — KV access to config_auth
    storage: Any               # StoragePort — the 14-repo facade
    apply_use_case: Any        # LegacyUseCasePort — apply pipeline
    prepare_use_case: Any      # LegacyUseCasePort — prepare pipeline
    presets: Any               # PresetsManager — moved from hh_applicant_tool.ui
    progress_sink: Callable[[int, int, str], None]   # pywebview _send_progress
    auth_event_sink: Callable[[str, str], None]      # pywebview _send_auth_event
    window: Any = None         # set by UiSlice.set_window()

class UiApiContextPort(Protocol):
    """Structural port — the Api class is duck-typed against this."""
    api_client: Any
    # ... same fields ...
```

```python
# src/job_bot/ui/api.py (slimmed)
class Api:
    def __init__(self, ctx: UiApiContextPort) -> None:
        self._ctx = ctx

    def get_resumes(self) -> list[dict]:
        return self._ctx.api_client.get("/resumes/mine")["items"]

    def save_config(self, updates: dict[str, Any]) -> dict[str, str]:
        # 30 methods, each a 1-3 line dispatch into the right port
        ...
```

```python
# src/job_bot/ui/slice.py
class UiSlice:
    def __init__(self, *, container: "AppContainer") -> None:
        self._container = container
        self._window: Any = None
        # Build the context once
        self._context = UiApiContext(
            api_client=container.api_client,
            config=container.config_auth_slice,
            storage=container.storage,
            apply_use_case=container.apply_to_vacancies_use_case(...),
            prepare_use_case=container.prepare_vacancies_use_case(...),
            presets=PresetsManager(storage=container.storage),
            progress_sink=self._send_progress,  # bound method
            auth_event_sink=self._send_auth_event,
        )

    def set_window(self, window: Any) -> None:
        self._window = window

    def build_api(self) -> Api:
        return Api(self._context)

def create_window(ui_slice: UiSlice, *, debug: bool = False) -> None:
    api = ui_slice.build_api()
    window = webview.create_window("HH Applicant Tool", "ui/index.html", js_api=api)
    ui_slice.set_window(window)
    webview.start(debug=debug)
```

**Why not a Protocol facade.** A `UiApiFacade` Protocol that `UiSlice` *implements* would mean `Api(Api(ui_slice))` (so JS can call `window.pywebview.api.some_method()`). The Protocol would have to declare 30 methods that mirror the JS bridge — a lot of boilerplate for no gain. The dataclass is a strict superset of what we need (the 30 methods are still on `Api`; the context is the *internal* wiring), and it's testable: `Api(UiApiContext(...))` can be unit-tested with a mock context.

### 5.4 Use case phase split — extracting handlers from `apply_to_vacancies` (issue #146)

**Question:** (a) extract one handler per phase into `handlers/`, or (b) split into multiple sub-slices (`application_submit.handlers.{apply,score,test,email}`)?

**Answer:** **(a) one handler per phase in `application_submit/handlers/`.** Sub-slices are a strong signal of "this is a separate bounded context with its own data and ports" — none of the 7 apply phases meet that bar. They're all part of *one* use case (the apply pipeline); they share storage (`ApplicationDraftRepo`), they share the API client, and they have a strict ordering (search → score → cover letter → skip-filter → email → captcha → submit). Splitting them into sub-slices would create 7 tiny slices that always move together, defeating the purpose of vertical slicing.

**Concrete shape (issue #146).**

The current `application_submit/handlers/` already has 4 files: `apply_one_handler.py`, `job_handler.py`, `test_handler.py`, `retry_handler.py`. After #146, it has 9:
- `apply_one_handler.py` (unchanged — already in slice)
- `test_handler.py` (unchanged)
- `job_handler.py` (unchanged)
- `retry_handler.py` (unchanged)
- **new** `search_handler.py` — wraps `_get_vacancies` + `_build_search_params`. Constructor: `(vacancy_search_handler, search_params_builder)`. Single public method: `iter_vacancies(profile) -> Iterator[Vacancy]`.
- **new** `score_handler.py` — wraps AI relevance filter. Constructor: `(relevance_handler, threshold)`. Single public method: `score(vacancy) -> RelevanceResult`.
- **new** `cover_letter_handler.py` — re-export of `application_prep.CoverLetterHandler`, thin adapter. Constructor: `(cover_letter_handler)`. Single public method: `generate(vacancy) -> str`.
- **new** `skip_handler.py` — wraps `_check_vacancy_skips` + blacklist filter. Constructor: `(storage, blacklisted_employers)`. Single public method: `should_skip(vacancy) -> SkipReason | None`.
- **new** `email_handler.py` — wraps `_send_email` + `_maybe_send_email`. Constructor: `(email_sender, config)`. Single public method: `maybe_send(vacancy, contact) -> None`.
- **new** `captcha_handler.py` — wraps `_solve_captcha_async`. Constructor: `(captcha_solver, captcha_ai)`. Single public method: `solve(captcha_url) -> bool`.

The use case is then reduced to a ~400-LOC orchestrator that calls these handlers via constructor DI:

```python
class ApplyToVacanciesUseCase:
    def __init__(self, *, search, score, cover_letter, skip, email, captcha, apply_one, ...):
        self._search = search
        self._score = score
        # ...

    def execute(self, command, cancel_event=None, progress_callback=None):
        for vacancy in self._search.iter_vacancies(command.profile):
            if self._skip.should_skip(vacancy):
                continue
            result = self._score.score(vacancy)
            if not result.accepted:
                continue
            cover_letter = self._cover_letter.generate(vacancy)
            ...
            self._apply_one(draft)
```

The `ApplicationSubmitSlice.run_apply_pipeline` is updated to call the in-slice handlers directly (not via the `LegacyUseCasePort`), so #155's deletion of `application/use_cases/` is straightforward.

**What we explicitly do *not* do:**
- We do *not* create a separate `application_submit.handlers.score` sub-slice. The AI relevance score uses the *same* `ApplicationDraftModel` and the *same* `VacancyModel` as the rest of the pipeline; splitting it out would force a cross-slice import for the model.
- We do *not* extract `apply_one_handler.py` into its own slice either, despite the bridge PR comment ("`apply_one` is the in-slice VSA equivalent of `services.apply_one`"). It's the natural end of the pipeline; it stays in `application_submit/handlers/`.

### 5.5 Entry point — `pyproject.toml` after the migration (issue #153)

**Question:** does the `hh-applicant-tool` script keep its name? Does the package stay as a stub for back-compat?

**Answer:** **Yes to both.**

**Script name:** `hh-applicant-tool` is the user-facing CLI name. Renaming it (e.g. to `job-bot`) would break every existing shell alias, CI script, Docker `CMD`, and tutorial. The cost is zero (`pyproject.toml` `[project.scripts]` is a 1-line edit) and the benefit is non-negative (preserves muscle memory). The script now points at `job_bot.cli.main:main`.

**Package stub:** `src/hh_applicant_tool/` is kept as a 5-LOC package after #155, because:
1. `pyproject.toml` `packages = [{ include = "hh_applicant_tool", from = "src" }]` would need to be removed if the package is fully deleted — a more visible break.
2. The `python -m hh_applicant_tool` invocation needs to still work (delegates to `python -m job_bot`).
3. The `import hh_applicant_tool` deprecation path is a 5-LOC `__init__.py` that emits a `DeprecationWarning` and re-exports the version constant. The next major version (3.0.0) can delete it.

**Concrete `pyproject.toml` diff (#153 + #155).**
```diff
 [project.scripts]
-hh-applicant-tool = "hh_applicant_tool.main:main"
+hh-applicant-tool = "job_bot.cli.main:main"
+
+[project.entry-points."job_bot.cli"]
+# 3rd-party plugins can register here
+BUILTIN = "job_bot.cli:BUILTIN_OPERATIONS"

 [tool.poetry.scripts]
-hh-applicant-tool = "hh_applicant_tool.main:main"
+hh-applicant-tool = "job_bot.cli.main:main"

 [tool.poetry]
-version = "1.8.10"
+version = "2.0.0"

 packages = [
   { include = "hh_applicant_tool", from = "src" },
+  { include = "job_bot", from = "src" },
 ]
```

The `hh_applicant_tool` package stays in `packages = [...]` (it's 5 LOC, it has a deprecation warning, removing it from the build is a SemVer-major-of-a-major change that 3.0.0 can do). The `job_bot` package is added.

**Why not delete `hh_applicant_tool` outright.** Two reasons:
1. **Distribution size:** the 5-LOC stub is 5 LOC; the sdist `tar.gz` is ~1 KB. The cost is noise.
2. **Forks and third-party extensions:** if a fork imports `from hh_applicant_tool import X` somewhere, the deprecation warning gives them a clear path to `job_bot.X`. A hard deletion would silently break forks. The 2.0.0 release notes can call out the 3.0.0 removal as the deadline.

**Script vs `python -m`.** The user can invoke the CLI three ways:
- `hh-applicant-tool` (the script — preferred, unchanged).
- `python -m job_bot` (the new `__main__.py`).
- `python -m hh_applicant_tool` (the legacy `__main__.py`, with deprecation warning).

All three work. `python -m hh_applicant_tool` is a bridge for the deprecation period.

---

## 6. Validation plan

| Phase | Issue(s) | Test command | Expected |
|-------|----------|--------------|----------|
| 1 | #143, #144 | `uv run ty check src/job_bot/` | clean |
| 1 | #143, #144 | `uv run pytest tests/vsa/test_storage_base_repository.py tests/vsa/test_vacancy_search_slice.py tests/vsa/test_application_prep_relevance_vsa_path.py` | 100% pass |
| 2 | #145, #146 | `uv run pytest tests/test_prepare_vacancies.py tests/test_apply_jobs.py` | pass (unchanged) |
| 2 | #145, #146 | `wc -l src/hh_applicant_tool/application/use_cases/{apply,prepare}_vacancies.py` | ≤500, ≤300 |
| 3 | #147, #148 | `uv run pytest tests/vsa/cli/` | 13 new test files pass |
| 3 | #149 | `uv run pytest tests/vsa/test_ui_slice.py` | pass |
| 3 | #149 | manual: `hh-applicant-tool ui` → `get_resumes()` round-trip in webview console | works |
| 3 | #150 | `grep -r 'from hh_applicant_tool.utils' src/` | empty (or only the deprecation re-exports) |
| 4 | #151, #152 | `grep -r 'from hh_applicant_tool.infrastructure\|from hh_applicant_tool.api' src/` | empty |
| 4 | #151, #152 | `uv run pytest` | 1014 pass (unchanged) |
| 5 | #153, #154 | `uv run hh-applicant-tool --help` | works |
| 5 | #153, #154 | `python -m job_bot --help` | works |
| 5 | #153, #154 | `wc -l src/hh_applicant_tool/container.py src/job_bot/container.py` | ≤400 each |
| 6 | #155 | `find src/hh_applicant_tool -type f` | `__init__.py`, `__main__.py` only |
| 6 | #155 | `python -c 'import hh_applicant_tool'` | emits `DeprecationWarning` |
| 6 | #155 | `uv run pytest` | 1014 - removed + 0 = 1014 pass (or as documented in #157) |
| 6 | #156 | `grep '^version' pyproject.toml` | `version = "2.0.0"` |
| 6 | #157 | `uv run pytest` | 1014 - removed = pass |
| 7 | #158 | `git log main..develop` | empty |
| 7 | #158 | `git tag --list 'v2.*'` | `v2.0.0` |

**Continuous gates (every PR):**
- `uv run ruff check src/ tests/`
- `uv run ty check src/job_bot/` (strict)
- `uv run pytest` (full test suite, 1014 currently passing + 7 xfailed)
- `uv run pytest -m integration` (cross-slice integration tests, opt-in)
- The deprecation contract test `tests/test_issue_92_deprecation.py` (which has an empty `SHIM_CONTRACT` after #155 — the test will need to be updated to handle that case).

---

## 7. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | **Touching `AppContainer` carelessly breaks 7 slices.** | High | High | Container changes are *only* in #154. Each PR is gated by `uv run pytest tests/vsa/test_*_slice.py` for all 7 slices. PR review requires running the full test suite. |
| R2 | **The UI's `_ProgressHandler` / `_send_progress` / `_send_auth_event` callbacks are pywebview-specific.** | Medium | High | The callbacks are isolated to `UiSlice.set_window()` and the `progress_sink` / `auth_event_sink` callables in `UiApiContext`. The 30+ business-logic methods don't touch them. #149 keeps the callback shape identical, just sourced from a different attribute. |
| R3 | **`pkgutil.iter_modules` removal breaks 3rd-party `Operation` plugins.** | Low | Medium | Issue #148 introduces `BUILTIN_OPERATIONS` as a tuple, but a fallback `iter_modules` walk over `job_bot.cli` can stay for one release with a `# DEPRECATED: prefer BUILTIN_OPERATIONS registration` comment. Documented in the migration guide. |
| R4 | **`pyproject.toml` script rename breaks user shell aliases.** | Medium | Medium | We *do not rename* the script; we only repoint it. The script name `hh-applicant-tool` is preserved (#153). The `python -m hh_applicant_tool` invocation also keeps working (via the #155 stub). |
| R5 | **The `BaseRepository` change in #143 silently breaks the 6 VSA repos that re-implement its abstract methods.** | Medium | High | #143 keeps the VSA `BaseRepository` as a `BaseSqliteRepository` *concrete* class — the 6 repos lose code (their re-implementations are removed), they don't gain breakage. The new tests in `tests/vsa/test_storage_base_repository.py` cover the inherited defaults. |
| R6 | **The `resume_md.py` move (#150) is large (611 LOC) and has only 1 consumer.** | Low | Low | The 1 consumer is `operations/create_resume` (shim, #137) which is *itself* being replaced in #147. The new `job_bot.resume_management.services.resume_renderer.py` can be a verbatim copy + 5-line import fix. |
| R7 | **The `main` ↔ `develop` reconciliation (#158) creates a bad merge commit.** | Medium | High | Option (2) — merge commit, not rebase — preserves `main`'s history. The merge commit message is "Release 2.0.0 — `hh_applicant_tool` retired". If the merge has conflicts, they are confined to `pyproject.toml` (version) and `ROADMAP.md` — both easy to resolve. |
| R8 | **Phase 2 (#145, #146) extracts handlers but the use cases still need the `LegacyUseCasePort`.** | High | Medium | The bridge stays in #145 and #146 (the use cases are *reduced*, not *deleted*). The port is removed only in #155. The handler-extraction PRs include explicit "what stays in the use case" sections in the PR body. |
| R9 | **The `Operation` class shape differs between legacy and VSA ops.** | High | Low | The 6 already-VSA ops use the legacy `BaseOperation` shape (`run(self, tool, args)`). #147 standardises on a new VSA `BaseOperation` shape (`run(self, args)` — slice deps are constructor-injected). The 6 ops are re-typed as part of #147. |
| R10 | **`#158` is irreversible.** | Certain | High | The merge commit is the natural cut line for 2.0.0. If something is wrong, the rollback is `git tag -d v2.0.0 && git reset --hard v1.8.10 && git push --force-with-lease origin main`. This is documented in the PR body and called out in `CHANGELOG.md`. |
| R11 | **The VSA `BaseRepository` is "dead" in some slices (they use the legacy `@dataclass` one).** | Medium | Medium | #143 makes the VSA one the canonical base, but does *not* delete the legacy `@dataclass` one. The legacy 13 model classes still extend the legacy `BaseRepository`. The deletion of `hh_applicant_tool/storage/` happens in #155. |
| R12 | **A 3rd-party fork imports a class that was in `hh_applicant_tool.api.errors` (e.g. `CaptchaRequired`).** | Medium | Medium | #152 moves `CaptchaRequired` to `application_submit.errors` (where `RetryableError` / `FatalError` already live). #155 keeps the 5-LOC `hh_applicant_tool/__init__.py` stub that emits a `DeprecationWarning` pointing to the new location. The 3.0.0 release can fully delete. |
| R13 | **The webview's `js/app.js` (886 LOC) calls into the `Api` class by method name.** | Low | High | The 30 method names on the slimmed `Api` are *unchanged*. #149 is a pure refactor: same public surface, different internal wiring. The JS doesn't know the difference. |
| R14 | **The `ApplicationSubmitAdapter` and friends in `AppContainer` are referenced by external code (none in-repo, but possibly by forks).** | Low | Medium | The adapters are in `src/hh_applicant_tool/container.py`, which is being *modified* in #154. They are deleted when their consumer (the legacy use case) is also deleted. Forks importing `_ApplicationSubmitAdapter` directly will see an `ImportError` — documented in the 2.0.0 release notes. |

---

## 8. Issue dependency matrix (compact)

```
#143 ── #144 ─┬─ #145 ─┬─ #146 ─┬─ #149 ─┐
              │         │         │         │
              │         │         │         ├─ #154 ─┐
              │         │         │         │         │
              ├─ #147 ── #148 ── #153 ──────┘         │
              │         │                             │
              │         └─ #155 ── #156 ── #157 ── #158
              │
              ├─ #150 ─────────────────────────────────┘
              │
              └─ #151 ── #152 ──────────────────────────┘
```

**Strict prerequisites:**
- #144 needs #143
- #145 needs #143, #144
- #146 needs #143, #144, #145
- #147 needs #143, #144
- #148 needs #147
- #149 needs #143, #144, #145, #146
- #150 needs #147
- #151 needs #143, #144, #145, #146
- #153 needs #148
- #154 needs #145, #146, #148
- #155 needs all of #143–#154
- #156 needs #155
- #157 needs #155
- #158 needs #156

**Critical path:** #143 → #144 → #145 → #146 → #149 → #154 → #155 → #156 → #158 (9 issues, ~5 weeks).
**Parallelisable:** #147, #148, #150, #151, #152, #153 (all branch off Phase 1-2 work).

---

## 9. Summary of the design's reversibility

| Action | Reversible? | How to revert |
|--------|-------------|---------------|
| Per-PR changes (issues #143–#157) | Yes | `git revert` the PR merge commit |
| `pyproject.toml` `[project.scripts]` repoint (#153) | Yes | `git revert` |
| `hh_applicant_tool/` package deletion (#155) | Yes (with the 5-LOC stub still in place) | restore from the previous commit |
| `main` ↔ `develop` merge (#158) | **No** | `git reset --hard` + `git push --force-with-lease` (destructive) |

The only **truly irreversible** step is #158. Everything else is revertable as long as we don't delete git history (which we won't — `--force-with-lease` is not used in this plan).
