# Project Roadmap

## Current Status: VSA Migration Complete ✅
- **7 slices extracted** with TDD
- **782 tests passing** (was 561 → +221)
- **Ruff clean**, MyPy baseline
- **All original issues #1-52 closed**

---

## Phase 1: Switchover (Priority: HIGH)
Make VSA slices the runtime source of truth, deprecate old `hh_applicant_tool` code.

| Issue | Task | Effort | Dependencies |
|-------|------|--------|--------------|
| #53 | Wire Vacancy Search slice + deprecate old collector | M | — |
| #54 | Wire Application Prep slice + deprecate old prepare | M | #53 |
| #55 | Wire Application Submit slice + deprecate old worker | M | #54 |
| #56 | Wire Telegram Bot slice + deprecate old operation | L | #55 |
| #57 | Wire Channel Monitoring slice + deprecate old code | S | #56 |
| #58 | Wire MAX Bot slice + deprecate old code | S | #57 |
| #59 | Wire Config/Auth slice + deprecate old config | M | #53 |
| — | **Remove dead code** (after all wired) | L | All above |

**Success Criteria:** All CLI operations use VSA slices; old `hh_applicant_tool/services/*` removed; 782 tests still pass.

---

## Phase 2: Features (Priority: MEDIUM)

| Issue | Task | Effort | Notes |
|-------|------|--------|-------|
| #47 | SOCKS5 proxy for Telegram bot | S | Unblocks local dev |
| #49 | Local dev stack (docker-compose.dev.yml) | M | One-command startup |
| #60 | MAX Bot - Real API integration | M | Research MAX Bot API |
| #61 | Channel Monitoring - Real implementation | M | Telegram Bot API channels |
| #62 | Rebranding (package rename) | M | job_bot / vacancy_agent |

---

## Phase 3: Quality (Priority: MEDIUM)

| Issue | Task | Effort |
|-------|------|--------|
| #63 | Integration tests (old vs new parity) | M |
| #64 | MyPy strict mode on VSA slices | M |
| #65 | Performance benchmarks & profiling | M |

---

## Phase 4: Production Hardening (Priority: LOW)

- Observability: OpenTelemetry, structured logs, Prometheus metrics
- Health endpoints: `/health`, `/ready`
- Rate limiting: Token bucket per HH API endpoint
- Secrets management: Vault/1Password integration
- CI/CD: GitHub Actions with full test suite

---

## Timeline (Estimated)

| Week | Focus |
|------|-------|
| 1-2 | **Phase 1**: Switchover (complete) |
| 3-4 | **Phase 2**: Features (SOCKS5, Local dev, MAX, Channels, Rebrand) |
| 5 | **Phase 3**: Quality (Integration tests, MyPy, Perf) |
| 6+ | **Phase 4**: Production hardening |

---

## Current Blockers
None. All infrastructure ready. VSA foundation stable.

---

## Next Action
Start **Issue #53** - Wire Vacancy Search slice into collector (first switchover).
