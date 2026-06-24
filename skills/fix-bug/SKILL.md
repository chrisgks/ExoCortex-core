---
name: fix-bug
description: Use when the failing path is known well enough to patch the defect conservatively, verify the fix, and reduce the chance of regression.
---

# Fix Bug

Patch a known defect with the smallest viable change and prove the regression is closed.

## Use This Skill When

- the failure is already reproduced or clearly bounded
- the task is corrective rather than open-ended feature work
- the main question is how to fix the defect safely

## Goals

- isolate the smallest viable fix
- preserve existing behavior outside the failing path
- verify the defect no longer reproduces

## Required Inputs

1. A concrete failing path from `skills/debug-failure/` or an equivalent repro.
2. A verification path from `skills/verification-discovery/`.
3. The relevant code and tests for the failing path.

## Workflow

1. Inspect the failing path before editing.
2. Implement the smallest viable fix.
3. Re-run the narrowest relevant verification first.
4. Add or strengthen regression coverage when needed.
5. Run broader checks only when they add real confidence.

## Evidence Rules

- tie the fix to the observed failure mode
- re-run the failing check after the patch
- report any remaining uncertainty or unverified edge cases

## Stop Conditions

- the defect is no longer reproducible and the relevant checks pass
- the defect cannot be fixed safely without requirement or design clarification

## Do Not

- do not turn a bug fix into unrelated cleanup
- do not clear the bug without re-running the failing path
