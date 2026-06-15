# VSA Finalisation Plan — `hh_applicant_tool` → `job_bot` Migration

> **Status:** plan (read-only) — no code changes yet
> **Date:** 2026-06-15
> **Target repo:** `q-user/hh_apply`
> **Working branch:** `develop`
> **Companion design doc (canonical, 945 LOC):** [`docs/vsa_finalisation_plan.md`](vsa_finalisation_plan.md)
> **Related docs:** [`ROADMAP.md`](../ROADMAP.md), [`docs/vsa_migration_guide.md`](../docs/vsa_migration_guide.md), [`tests/test_issue_92_deprecation.py`](../tests/test_issue_92_deprecation.py)

---

## 0. TL;DR

The VSA switchover is complete. `src/job_bot/` ships 12 slices + a `shared` kernel. The legacy `src/hh_applicant_tool/` is reduced to:

- 5 deprecation shims tracked by `tests/test_issue_92_deprecation.py:SHIM_CONTRACT`
- 6 active CLI ops that delegate to VSA slices via `AppContainer`
- 13 small un-migrated CLI ops (`call_api`, `whoami`, `list_resumes`, `install`, `uninstall`, `migrate_db`, etc.)
- The webview UI (`ui/api.py` 673 LOC, still coupled to `HHApplicantTool`)
- The 1151-LOC `AppContainer` composition root + 613-LOC `HHApplicantTool` CLI facade
- 8 active `infrastructure/*` port implementations (no VSA equivalent)
- 13 storage `models/*` + 15 `repositories/*` (own the SQL schema)
- 5 `utils/*` helpers (no VSA home yet)
- The 1344-LOC `apply_to_vacancies` and 989-LOC `prepare_vacancies` use cases (partial VSA bridges)

**Plan:** file **16 new GitHub issues** (#144–#159) in milestone `VSA Finalisation` (#7, new). Execute in **7 phases** over ~5–6 weeks. End state: `hh_applicant_tool/` is a 5-LOC deprecation stub, `AppContainer` lives at `src/job_bot/container.py`, the script `hh-applicant-tool` is preserved, the version is bumped to 2.0.0, and `main` is reconciled with `develop` for the 2.0.0 release.

---

## 1. Milestone

**Create one new milestone** in `q-user/hh_apply`:

| Field | Value |
|---|---|
| Title | `VSA Finalisation` |
| Description | `Complete the VSA migration by deleting hh_applicant_tool. Builds on milestone #6 (VSA Migration) and the bridge PRs #129–#134. Tracks the 16-issue plan in docs/vsa_finalisation_plan.md. The 2.0.0 release is the natural cut line.` |
| Due date | `2026-07-14` |
| State | `open` |

**Keep milestone #6 `VSA Migration` in place** for historical context (it has 19 closed issues, 0 open). Its description is misleading (the "### Switchover" list marks #56–#58 as `TODO` when they are closed; the "### Already completed" list grows stale) but re-purposing it would require heavy editing and conflate different work.

**Do not file the new issues yet** — see "Phased execution" below for the order. The `gh issue create` commands are drop-in ready from the design doc bodies.

---

## 2. The 16 issues (filed in order)

All issues use these labels: **`vsa` + `refactoring` + `tech-debt`** (plus `breaking` for #157, `ui` for #152, `cli` for #150).

### Phase 1 — Shared kernel (foundation, ~1 week)

| # | Title | Target | Depends on | Size |
|---|-------|--------|------------|------|
| #144 | `refactor(vsa): make VSA BaseRepository (ABC) the canonical base` | `job_bot.shared.storage.repository` | — | M |
| #145 | `refactor(vsa): fill out shared/storage/facade.py with all 14 repository properties` | `job_bot.shared.storage` | #144 | S |

**Why first:** the slice repos that exist today inherit a dead ABC; the `StorageFacade` is a `@dataclass` with all 14 repo attributes commented out. Before we extract per-phase handlers from the use cases, the storage foundation must be solid.

### Phase 2 — Use case phase split (~2 weeks, highest risk)

| # | Title | Target | Depends on | Size |
|---|-------|--------|------------|------|
| #146 | `refactor(vsa): port application/use_cases/prepare_vacancies per-phase handlers to VSA` | `job_bot.application_prep` | #144, #145 | L |
| #147 | `refactor(vsa): port application/use_cases/apply_to_vacancies per-phase handlers to VSA` | `job_bot.application_submit` | #144, #145, #146 | L |

**Why next:** the 1344-LOC `apply_to_vacancies` and 989-LOC `prepare_vacancies` use cases own ~800 LOC of inline phase logic each. Extract one handler per phase (search / score / cover_letter / skip / email / captcha) into `application_submit/handlers/`. The use cases shrink to ~400 LOC orchestrators. The `LegacyUseCasePort` Protocol in `ApplicationSubmitSlice` is removed in #147.

### Phase 3 — CLI + UI migration (~1.5 weeks)

| # | Title | Target | Depends on | Size |
|---|-------|--------|------------|------|
| #150 | `feat(cli): introduce job_bot.cli package with the 13 un-migrated operations` | `job_bot.cli` (new) | #144, #145 | M |
| #148 | `refactor(vsa): port utils/{cookiejar,mixins,resume_md,terminal} to shared/utils and application_prep/services` | `shared/utils`, `application_prep.services` | #150 | M |
| #151 | `refactor(vsa): replace pkgutil.iter_modules CLI dispatch with a static registry` | `job_bot.cli` | #150 | S |
| #152 | `refactor(vsa): decouple ui/api.py from HHApplicantTool via a UiApiContext port` | `job_bot.ui` (new) | #144, #145, #146, #147 | L |

**Why this phase:** Phase 2 unblocks the UI (slim use cases mean the `_ApplicationPrepAdapter` / `_ApplicationSubmitAdapter` shims in `AppContainer` can be deleted). The 13 un-migrated CLI ops get a VSA home in `job_bot.cli`. The 673-LOC `ui/api.py` is rewritten against a `UiApiContext` `@dataclass` (preserves the 30-method public surface that pywebview's JS bridge relies on).

### Phase 4 — Infrastructure housekeeping (~3 days, parallel with Phase 3)

| # | Title | Target | Depends on | Size |
|---|-------|--------|------------|------|
| #153 | `refactor(vsa): port infrastructure/* to shared/infrastructure/ (or per-slice services/)` | `shared/infrastructure` (new), per-slice | #144, #145, #146, #147 | M |
| #154 | `refactor(vsa): port api/datatypes.py and api/errors.py to shared/api/` | `shared/api` | — | S |

**Why this phase:** `infrastructure/*` and `api/*` are runtime-active, not blocking, but moving them now keeps the #157 diff small.

### Phase 5 — Entry point + composition root (~3 days)

| # | Title | Target | Depends on | Size |
|---|-------|--------|------------|------|
| #155 | `feat(vsa): add VSA-native __main__.py and switch [project.scripts] entry point` | `job_bot.__main__` | #151 | S (breaking) |
| #156 | `refactor(vsa): slim AppContainer to a pure VSA composition root` | `job_bot.container` (new) | #146, #147, #151 | M |

**Why this phase:** with the use cases slimmed (#146, #147) and the CLI dispatch static (#151), the `AppContainer` adapters are redundant and the entry point can switch.

### Phase 6 — Package deletion + release prep (~2 days)

| # | Title | Target | Depends on | Size |
|---|-------|--------|------------|------|
| #157 | `refactor(vsa): delete hh_applicant_tool package, leave a 5-line stub` | n/a (deletion) | #144–#156 | L (breaking) |
| #158 | `chore: bump version to 2.0.0 (SemVer major)` | `pyproject.toml` | #157 | S |
| #159 | `chore: reconcile main with develop (close 119-commit gap) — Release 2.0.0` | git | #158 | S (**irreversible**) |

*(Issue #149 in the design doc was renamed here to #157–#159 to keep issue numbers monotonic with the recent #143, and to split the original "deletion + version bump + main reconciliation" into 3 separate issues for cleaner PR review.)*

### Issue #157 — package deletion

```python
# src/hh_applicant_tool/__init__.py  (5 LOC)
import warnings
warnings.warn(
    "hh_applicant_tool is deprecated; use job_bot instead. "
    "Removed in 2.0.",
    DeprecationWarning, stacklevel=2,
)
__all__ = ["__version__"]
__version__ = "2.0.0"
```

```python
# src/hh_applicant_tool/__main__.py  (5 LOC)
import warnings
warnings.warn(
    "hh_applicant_tool.__main__ is deprecated; use 'python -m job_bot'.",
    DeprecationWarning, stacklevel=2,
)
from job_bot.__main__ import main
import sys
sys.exit(main())
```

`pyproject.toml` after #157:

```diff
 [project.scripts]
-hh-applicant-tool = "hh_applicant_tool.main:main"
+hh-applicant-tool = "job_bot.cli.main:main"

 [tool.poetry.scripts]
-hh-applicant-tool = "hh_applicant_tool.main:main"
+hh-applicant-tool = "job_bot.cli.main:main"

 packages = [
   { include = "hh_applicant_tool", from = "src" },
+  { include = "job_bot", from = "src" },
 ]
```

The `hh-applicant-tool` script name is **preserved** (no shell-alias break). The `hh_applicant_tool` package stays as a 5-LOC stub (forks get a clear deprecation warning).

---

## 3. Phased execution roadmap

```
Phase 1 (#144 → #145)            Shared kernel
       ↓
Phase 2 (#146 → #147)            Use case phase split
       ↓                ↘  #153, #154 (parallel — housekeeping)
Phase 3 (#150 → #148, #151, #152)  CLI + UI migration
       ↓
Phase 5 (#155, #156)             Entry point + composition root
       ↓
Phase 6 (#157 → #158 → #159)     Deletion + version bump + main reconciliation
```

**Critical path:** #144 → #145 → #146 → #147 → #152 → #156 → #157 → #158 → #159 (9 issues, ~5 weeks)
**Parallelisable:** #148, #150, #151, #153, #154, #155 (all branch off Phase 1-2 work)

**Total:** 16 issues, ~5–6 weeks of focused agent work, `develop` stays mergeable at every step.

---

## 4. Critical files to be modified

| Path | Operation | Issue |
|------|-----------|-------|
| `src/job_bot/shared/storage/repository.py` | rewrite (concrete class, not ABC) | #144 |
| `src/job_bot/shared/storage/facade.py` | fill out 14 repos | #145 |
| `src/job_bot/shared/storage/ports.py` | update `StoragePort` Protocol | #145 |
| `src/job_bot/application_prep/services/` | new (4 service files) | #146 |
| `src/job_bot/application_submit/handlers/` | new (5 handler files) | #147 |
| `src/hh_applicant_tool/application/use_cases/apply_to_vacancies.py` | 1344 → ≤500 LOC | #147 |
| `src/hh_applicant_tool/application/use_cases/prepare_vacancies.py` | 989 → ≤300 LOC | #146 |
| `src/job_bot/cli/` | new (13 sub-commands + registry) | #150, #151 |
| `src/job_bot/ui/` | new (`__init__.py`, `api.py` slimmed, `ports.py`, `presets.py`, `slice.py`, `templates/`) | #152 |
| `src/hh_applicant_tool/ui/api.py` | 673 → ~400 LOC | #152 |
| `src/hh_applicant_tool/ui/templates/` | move to `src/job_bot/ui/templates/` | #152 |
| `src/job_bot/shared/infrastructure/` | new (or per-slice) | #153 |
| `src/job_bot/shared/api/datatypes.py` | move from `hh_applicant_tool/api/datatypes.py` | #154 |
| `src/job_bot/shared/api/errors.py` | move from `hh_applicant_tool/api/errors.py` | #154 |
| `src/job_bot/__main__.py` | new (5 LOC) | #155 |
| `src/job_bot/cli/main.py` | new (the new entry point, ~100 LOC) | #155 |
| `src/job_bot/container.py` | new (slimmed from 1151 → ~400 LOC) | #156 |
| `src/hh_applicant_tool/container.py` | delete | #157 |
| `src/hh_applicant_tool/main.py` | delete | #157 |
| `src/hh_applicant_tool/operations/` | delete (entire directory) | #157 |
| `src/hh_applicant_tool/storage/` | delete | #157 |
| `src/hh_applicant_tool/infrastructure/` | delete | #157 |
| `src/hh_applicant_tool/api/` | delete | #157 |
| `src/hh_applicant_tool/application/` | delete | #157 |
| `src/hh_applicant_tool/ai/` | delete | #157 |
| `src/hh_applicant_tool/utils/{cookiejar,mixins,resume_md,terminal}.py` | delete | #148 |
| `src/hh_applicant_tool/__init__.py` | reduce to 5-LOC stub | #157 |
| `src/hh_applicant_tool/__main__.py` | reduce to 5-LOC stub | #157 |
| `pyproject.toml` | bump to 2.0.0, repoint scripts, add `job_bot` package | #155, #157, #158 |
| `ROADMAP.md` | mark Phase D as DONE, add Phase F (release) | #157 |
| `docs/vsa_migration_guide.md` | update shim-removed section | #157 |
| `tests/test_issue_92_deprecation.py` | empty `SHIM_CONTRACT` (the 5 rows are gone) | #157 |
| `git: main ↔ develop` | merge commit + `v2.0.0` tag | #159 |

---

## 5. The 5 design decisions

### 5.1 Storage: keep the per-slice repos, fill out the facade

- **Make `BaseRepository` (ABC) concrete** by renaming to `BaseSqliteRepository` with rich defaults (find, save, save_batch, count_total, delete, clear) inherited verbatim from `hh_applicant_tool/storage/repositories/base.py`. The 6 existing VSA repos lose ~50 LOC each as their re-implementations are replaced.
- **Keep the per-slice `repositories/` pattern** (each slice owns its data shape) but add a thin `StorageFacade` aggregator so cross-slice consumers depend on a single `StoragePort`.
- **Do not delete the legacy `BaseRepository` (@dataclass) in #144** — the legacy 13 model classes still extend it. The deletion happens in #157.

### 5.2 CLI dispatch: static `BUILTIN_OPERATIONS` tuple

- **Drop `pkgutil.iter_modules`.** It couples the CLI to the *physical* location of operations and forces eager import.
- **Use a static `BUILTIN_OPERATIONS: tuple[type[BaseOperation], ...]`** exported from `job_bot.cli`. A 3rd-party plugin appends to the tuple (or to a separate `EXTERNAL_OPERATIONS` list).
- **Re-type the 6 already-VSA ops** (`apply_vacancies`, `apply_worker`, `channel_monitor`, `max_bot`, `telegram_bot`, `prepare_vacancies`) as VSA `Operation` classes — they take their slice deps via `__init__`, not via `tool: HHApplicantTool`.

### 5.3 UI: `UiApiContext` `@dataclass`, not a Protocol facade

- A Protocol facade would force `Api` to be a class implementing 30+ methods *as well as* a Protocol; that's two ways to do the same thing.
- The pywebview `js_api` protocol **requires** `Api` to be a class with public methods (JS calls `window.pywebview.api.method_name(...)`), so a Protocol-as-`Api`-interface doesn't work.
- **`UiApiContext` `@dataclass`** bundles the dependencies `Api` actually uses (api_client, config, storage, apply_use_case, prepare_use_case, presets, progress_sink, auth_event_sink, window). `Api.__init__` unpacks it. The 30 method names are unchanged — `js/app.js` doesn't need to change.

### 5.4 Use case phase split: handlers in `application_submit/handlers/`, not sub-slices

- The 7 apply phases (search, score, cover letter, skip, email, captcha, apply_one) all share storage, API client, and ordering. Sub-slices are for separate bounded contexts — these are not.
- **One handler per phase in `application_submit/handlers/`.** The 5 new files (`search_handler`, `score_handler`, `cover_letter_handler`, `skip_handler`, `email_handler`, `captcha_handler`) join the 4 existing (`apply_one_handler`, `job_handler`, `test_handler`, `retry_handler`).
- **The use case is reduced to a ~400-LOC orchestrator** that calls the handlers via constructor DI. The `LegacyUseCasePort` Protocol in `ApplicationSubmitSlice` is removed in #147.

### 5.5 Entry point: keep `hh-applicant-tool` script name, keep `hh_applicant_tool` as 5-LOC stub

- **`hh-applicant-tool` script name is preserved** (no shell-alias break for users). Repointed to `job_bot.cli.main:main`.
- **`src/hh_applicant_tool/` is kept as a 5-LOC stub** that emits a `DeprecationWarning` on import. The next major version (3.0.0) can fully delete.
- **Three invocation paths all work**:
  - `hh-applicant-tool` (the script, preferred, unchanged name)
  - `python -m job_bot` (the new `__main__.py`)
  - `python -m hh_applicant_tool` (legacy bridge, with deprecation warning)

---

## 6. Validation plan

**Continuous gates (every PR):**
```bash
uv run ruff check src/ tests/
uv run ty check src/job_bot/        # strict
uv run pytest                         # 1014 passing + 7 xfailed currently
uv run pytest -m integration          # cross-slice integration tests
```

**Phase gates:**

| Phase | Issue(s) | Test command | Expected |
|-------|----------|--------------|----------|
| 1 | #144, #145 | `uv run ty check src/job_bot/` | clean |
| 1 | #144, #145 | `uv run pytest tests/vsa/test_storage_base_repository.py` | pass |
| 2 | #146, #147 | `uv run pytest tests/test_prepare_vacancies.py tests/test_apply_jobs.py` | pass |
| 2 | #146, #147 | `wc -l src/hh_applicant_tool/application/use_cases/{apply,prepare}_vacancies.py` | ≤500, ≤300 |
| 3 | #150, #151 | `uv run pytest tests/vsa/cli/` | 13 new test files pass |
| 3 | #152 | manual: `hh-applicant-tool ui` → webview opens, JS round-trip | works |
| 4 | #153, #154 | `grep -r 'from hh_applicant_tool.infrastructure\|from hh_applicant_tool.api' src/` | empty |
| 5 | #155, #156 | `uv run hh-applicant-tool --help` AND `python -m job_bot --help` | both work |
| 5 | #155, #156 | `wc -l src/job_bot/container.py` | ≤400 |
| 6 | #157 | `find src/hh_applicant_tool -type f` | `__init__.py`, `__main__.py` only |
| 6 | #157 | `python -c 'import hh_applicant_tool'` | emits `DeprecationWarning` |
| 6 | #158 | `grep '^version' pyproject.toml` | `version = "2.0.0"` |
| 6 | #159 | `git log main..develop` | empty |
| 6 | #159 | `git tag --list 'v2.*'` | `v2.0.0` |

---

## 7. Risk register (top 5)

| # | Risk | Mitigation |
|---|------|------------|
| R1 | Touching `AppContainer` carelessly breaks 7 slices (#156) | Container changes are *only* in #156. Every PR is gated by `pytest tests/vsa/test_*_slice.py` for all 7 slices. |
| R2 | UI's pywebview callbacks (`_ProgressHandler`, `_send_progress`, `_send_auth_event`) are webview-specific (#152) | Callbacks are isolated to `UiSlice.set_window()` and the `progress_sink` / `auth_event_sink` callables in `UiApiContext`. The 30+ business-logic methods don't touch them. |
| R7 | `main` ↔ `develop` reconciliation creates a bad merge commit (#159) | Use merge commit (not rebase). Conflicts are confined to `pyproject.toml` (version) and `ROADMAP.md` — both easy to resolve. |
| R8 | Phase 2 (#146, #147) extracts handlers but use cases still need `LegacyUseCasePort` | The bridge stays in #146 and #147 (use cases are *reduced*, not *deleted*). The port is removed only in #157. |
| R10 | #159 is irreversible | Documented in PR body and `CHANGELOG.md`. Rollback is `git tag -d v2.0.0 && git reset --hard v1.8.10 && git push --force-with-lease origin main`. |

Full 14-risk register: §7 of the [design doc](vsa_finalisation_plan.md).

---

## 8. What to file first

The user is in plan mode — no `gh` calls should be made yet. When ready to execute:

```bash
# 1. Create the new milestone
gh api repos/q-user/hh_apply/milestones \
  -f title="VSA Finalisation" \
  -f description="Complete the VSA migration by deleting hh_applicant_tool. ..." \
  -f due_on="2026-07-14"

# 2. File issues #144–#159 in order, in worktree branches per the
#    `agentic-vsa-workflow` skill convention. Use the bodies from
#    §2 of the design doc, drop-in ready.
```

The issue bodies in [`docs/vsa_finalisation_plan.md` §2](vsa_finalisation_plan.md#2-proposed-github-issues) are drop-in ready for `gh issue create --milestone "VSA Finalisation" --label vsa,refactoring,tech-debt --title "..." --body "..."`.

---

## 9. End state

After all 16 issues land and #159 ships the release:

- `src/hh_applicant_tool/` is a 5-LOC stub with a `DeprecationWarning` (removed in 3.0.0)
- `src/job_bot/` owns 12 slices + a `shared` kernel + `cli/` + `ui/` + `container.py` + `__main__.py`
- `pyproject.toml` script: `hh-applicant-tool = "job_bot.cli.main:main"` (unchanged name)
- `pyproject.toml` version: `2.0.0`
- `tests/vsa/`: all tests, no legacy coupling
- `tests/test_issue_92_deprecation.py`: empty `SHIM_CONTRACT` (no shims left)
- `git`: `v2.0.0` tag on the merge commit, `main` and `develop` reconciled
- `ROADMAP.md` Phase D: marked DONE
- `docs/vsa_migration_guide.md`: "## End state" section added
- 3rd-party forks: clear deprecation warning when they import from `hh_applicant_tool`

The 12-month migration arc that started with #50 (2026-06-10) closes here.
