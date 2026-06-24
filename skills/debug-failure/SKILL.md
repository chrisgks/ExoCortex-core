---
name: debug-failure
description: Use when the task starts with a vague failure report, broken behavior, or unclear symptom that must be turned into a concrete failing path before a fix can be trusted.
---

# Debug Failure

Turn a vague symptom into a concrete failing path or explicit blocker.

## Use This Skill When

- the task is “it’s broken” rather than a known defect
- the failure is intermittent, underspecified, or only partly reproduced
- `fix-bug` would be premature because the root cause is not yet clear

## Goals

- reproduce or bound the failure
- isolate the failing subsystem or path
- reduce ambiguity before editing

## Required Inputs

1. The reported symptom or failing behavior.
2. The relevant repo slice from `skills/repo-intake/`.
3. The current verification options from `skills/verification-discovery/`, if any.

## Workflow

1. Restate the failure as an observable behavior.
2. Reproduce it with the smallest available command, test, or direct check.
3. Narrow the problem to the smallest plausible subsystem.
4. Capture the current failure mode, likely cause, and missing evidence.
5. Hand off to `skills/fix-bug/` only after the failing path is concrete enough.

## Evidence Rules

- record what actually failed, not just what seems likely
- separate reproduction, hypothesis, and root cause
- if reproduction is impossible, report the blocker explicitly

## Stop Conditions

- the failure path is concrete enough for a targeted fix
- the repo or environment prevents meaningful reproduction

## Do Not

- do not patch speculatively before the failing path is bounded
- do not confuse a hunch with a reproduced defect
