# Builder Workflows

## Existing Codebase Change

1. Restate the task, target behavior, and likely done condition.
2. Read local context and relevant code.
3. Run `skills/repo-intake/` if the repo or stack is still unclear.
4. Run `skills/verification-discovery/` before editing.
5. Surface any assumptions and ask only blocking questions.
6. Implement the smallest viable change.
7. Verify with tests, builds, lint, type-checks, or direct checks.
8. Iterate until success is demonstrated or a blocker is explicit.
9. Summarize results, evidence, assumptions, and remaining risk.

## Bug Loop

1. Use `skills/debug-failure/` to turn the symptom into a concrete failing path.
2. Use `skills/fix-bug/` to isolate the defect and patch minimally.
3. Add or strengthen tests when coverage is missing.
4. Re-run targeted verification before declaring the bug fixed.

## Feature Loop

1. Confirm the expected behavior from local contracts and existing code.
2. Use `skills/implement-feature/` to extend existing patterns incrementally.
3. Ask only when product intent or behavior choice is still materially ambiguous.
4. Use `skills/add-tests/` when behavior changes are not already covered.
5. Run the narrowest trustworthy checks first, then broader checks as needed.

## Blocked Environment Loop

1. Determine whether the blocker is missing tooling, missing dependencies,
   broken setup, or insufficient permissions.
2. Prefer repo-local commands and existing setup instructions over improvisation.
3. If safe recovery is possible, continue.
4. If safe recovery is not possible, stop with:
   - the exact blocker
   - commands attempted
   - the next required human or agent action
