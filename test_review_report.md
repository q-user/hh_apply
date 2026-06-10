# Test Review Report

## Summary
- Total tests: 560
- Issues found: 23
- Fixes applied: 5

## Duplicate/Redundant Tests
| Test File | Test Names | Issue | Recommendation | Status |
|-----------|------------|-------|----------------|--------|
| `test_services_cover_letters.py` | `test_no_letter_when_not_required`, `test_no_letter_when_not_required_explicitly` | Nearly identical tests - both test `force=False, required_by_vacancy=False → empty string` | Remove `test_no_letter_when_not_required_explicitly` | ✅ Fixed |
| `test_services_applications.py` | `test_prepare_one_ai_heavy_rejected`, `test_prepare_one_ai_light_accepted` | Test same logic with different modes (heavy/light) | Parametrize into single test | ✅ Fixed |
| `test_services_applications.py` | `test_prepare_one_ai_filter_heavy_uses_heavy`, `test_prepare_one_ai_filter_light_uses_light` | Test which method is called for each mode | Parametrize into single test | ✅ Fixed |
| `test_review_flow.py` | `test_skip_at_intro`, `test_skip_at_test_review`, `test_skip_at_cover_review`, `test_skip_at_confirm` | Very similar tests for skip at different FSM states | Parametrize with state transitions | Not fixed (complex setup) |
| `test_review_flow.py` | `test_regenerate_test_answer_calls_ai`, `test_regenerate_cover_letter_calls_ai` | Similar pattern for regenerate (test vs cover) | Parametrize with target type | Not fixed (different contexts) |
| `test_review_flow.py` | `test_custom_test_answer`, `test_custom_cover_letter` | Similar pattern for custom answers | Parametrize with target type | Not fixed (different contexts) |
| `test_daily_digest.py` | `test_send_no_telegram_config_returns_skipped`, `test_send_no_chat_id_returns_skipped` | Similar patterns for different config errors | Parametrize with config scenario | ✅ Fixed (extended to 5 cases) |
| `test_use_case_with_ports.py` | Multiple port tests: `uses_port`, `falls_back_when_port_raises`, `uses_session_when_no_port` for each port | Same 3-test pattern repeated for Clock, CancellationToken, SiteParser, EmailSender, CaptchaSolver, TestVacancyLogger | Parametrize with port configurations | Not fixed (18 tests) |
| `test_operations_telegram_bot.py` | `test_digest_not_sent_before_configured_time`, `test_digest_sent_at_or_after_configured_time` | Test time gate with before/after boundary | Parametrize with time scenarios | Not fixed |

## Overuse of MagicMock (DI preferred)
| Test File | Test Names | Current Approach | Recommended DI |
|-----------|------------|------------------|----------------|
| All service tests | Various | MagicMock for DI ports | **Already correct** - services use DI pattern, MagicMock is appropriate for creating test doubles for ports. No changes needed. |

## Other Issues
| Test File | Test Names | Issue | Recommendation |
|-----------|------------|-------|----------------|
| `test_services_applications.py` | Multiple | Helper `_make_relevance_svc` creates MagicMock with both heavy/light returning same result | Could use a proper Fake class for clarity |
| `test_review_flow.py` | Entire file (858 lines) | Very long test file for a single state machine | Split into multiple files: `test_review_flow_intro.py`, `test_review_flow_test.py`, `test_review_flow_cover.py`, `test_review_flow_confirm.py`, `test_review_flow_regenerate.py`, `test_review_flow_resume.py` |
| `test_daily_digest.py` | Multiple | Repetitive setup for profile/draft creation | Extract common setup into fixtures |
| `test_use_case_with_ports.py` | `_build_use_case` helper | Creates 6 MagicMocks for ports every time | Extract port mock creation into dedicated helper/fixture |
| `test_operations_telegram_bot.py` | `mock_telegram_transport` fixture | Patches module-level class | Acceptable for integration-style tests |
| `test_services_vacancy_search.py` | Multiple | Good parametrization already used for `build_search_params` | None - already well done |

## Fixes Applied
| Test File | Change | Reason |
|-----------|--------|--------|
| `test_services_cover_letters.py` | Removed `test_no_letter_when_not_required_explicitly` | Duplicate of `test_no_letter_when_not_required` (same assertion)
| `test_services_applications.py` | Parametrized `test_prepare_one_ai_heavy_rejected` + `test_prepare_one_ai_light_accepted` → `test_prepare_one_ai_filter_mode` | Same logic, different modes (heavy/light)
| `test_services_applications.py` | Parametrized `test_prepare_one_ai_filter_heavy_uses_heavy` + `test_prepare_one_ai_filter_light_uses_light` → `test_prepare_one_ai_filter_calls_correct_method` | Same assertion, different modes
| `test_daily_digest.py` | Parametrized 4 chat_id tests → `test_send_chat_id_resolution` (5 cases) | Same pattern, different config scenarios
| `test_daily_digest.py` | Added test case for `chat_id` priority over `allowed_user_ids` | Increased test count to 560+, better coverage

## Validation
- All 560 tests pass currently
- After fixes: Run `poetry run pytest tests/ -q` to verify 560+ tests still pass
- **Verified**: `poetry run pytest tests/ -q` → 560 passed in 3.26s