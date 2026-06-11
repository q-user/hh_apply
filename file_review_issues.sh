#!/usr/bin/env bash
# File concrete code review findings as GitHub issues.

set -euo pipefail

echo "--- Creating issue: new type:ignore[arg-type] in facade init ---"
gh issue create \
  --title "fix(type-safety): StorageFacade(self._storage) uses # type: ignore[arg-type]" \
  --label "tech-debt,refactoring" \
  --body "## Description

During the code review of the last 5 commits (6b4a9c2 mypy-strict merge, a498bc7 spec #55, de5cccb urllib3 fix, 07b2aec TODO cleanup, cd8090d SUCKASS cleanup), a new \`# type: ignore[arg-type]\` was introduced:

\`\`\`python
facade = StorageFacade(self._storage)  # type: ignore[arg-type]
\`\`\`

This is in a new context (likely the \`storage/ports.py\` or \`digest_handler.py\` wiring added during the VSA migration). The \`self._storage\` value is being passed where the expected type signature requires a different type (probably \`sqlite3.Connection\` instead of a \`StorageFacade\`).

## Why this matters

- \`# type: ignore\` comments silently disable mypy's type checking, hiding real bugs.
- The 2 new \`# type: ignore\` instances in the last 5 commits are above the baseline. If the trend continues, the mypy-strict mode (issue #64) will be undermined.
- The root cause is likely a type-narrowing gap in the \`StorageFacade\` constructor or a mismatch between what the caller has and what the callee expects.

## Proposed fix

1. Identify why \`self._storage\` doesn't match the expected type (run \`mypy src/ --strict\` to see the full error).
2. Either:
   a. Add a proper type-narrowing assertion (\`assert isinstance(self._storage, ExpectedType)\`).
   b. Fix the \`StorageFacade\` constructor to accept the broader type.
   c. Update the caller's type annotation to match what it actually holds.
3. Remove the \`# type: ignore[arg-type]\` comment.
4. Verify \`mypy src/\` passes on the file without the ignore.

## Acceptance criteria

- [ ] Root cause of the type mismatch identified and documented in a comment.
- [ ] \`# type: ignore[arg-type]\` removed.
- [ ] \`uv run --frozen mypy src/\` passes on the affected file.
- [ ] No behavior change."

echo ""
echo "--- Creating issue: new noqa: BLE001 (broad except) ---"
gh issue create \
  --title "fix(error-handling): new `except Exception as ex:  # noqa: BLE001` introduced in last 5 commits" \
  --label "tech-debt,refactoring" \
  --body "## Description

During the code review of the last 5 commits, a new \`except Exception as ex:  # noqa: BLE001\` was introduced (in the AI-related service code, likely the relevance/cover-letter or digest handler).

\`\`\`python
except Exception as ex:  # noqa: BLE001
    # ... broad handler
\`\`\`

This is related to issue #69 (excessive bare \`except Exception\` in business logic), but it's a new occurrence introduced AFTER the original audit, so it warrants its own tracking.

## Why this matters

- Broad \`except Exception\` swallows unexpected errors silently.
- The \`# noqa: BLE001\` suppression hides the anti-pattern from linters.
- New occurrences increase the total count, making the cleanup effort larger.

## Proposed fix

1. Identify what specific exceptions are expected (e.g., \`AIError\`, \`TimeoutError\`, \`ConnectionError\`).
2. Replace the broad catch with specific exception types.
3. For truly unexpected errors, let them propagate (don't catch) or log with \`logger.exception()\` instead of \`logger.warning()\`.
4. Remove the \`# noqa: BLE001\` comment.

## Acceptance criteria

- [ ] Specific exception types listed in the \`except\` clause.
- [ ] \`# noqa: BLE001\` removed.
- [ ] \`ruff check src/\` passes on the affected file.
- [ ] Existing tests still pass."

echo ""
echo "--- VERIFY ISSUES CREATED ---"
gh issue list --state open --limit 5 --json number,title | python3 -c '
import json, sys
issues = json.load(sys.stdin)
for i in issues[:5]:
    print(f"  #{i[\"number\"]}  {i[\"title\"]}")
'
