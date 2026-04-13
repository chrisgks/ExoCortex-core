---
name: add-tests
description: Use when the current repo lacks enough verification for a change and the builder needs to add or tighten tests so success is supported by executable evidence rather than confidence alone.
---

# Add Tests

Improve verification so the change can be trusted.

## Use This Skill When

- the target behavior is weakly covered or uncovered
- a bug fix needs regression protection
- the builder cannot prove success with existing checks alone

## Goals

- add the narrowest useful coverage
- align test shape with the project’s existing style
- make the change easier to verify in future sessions

## Required Inputs

1. The target behavior or defect.
2. The project’s current test patterns and runner.
3. The relevant verification path from `skills/verification-discovery/`.

## Workflow

1. Inspect existing tests near the changed path.
2. Prefer the repo’s existing test framework and style.
3. Add the smallest test coverage that proves the new or corrected behavior.
4. Run the new or updated tests directly.
5. Re-run any broader relevant verification if needed.

## Evidence Rules

- state whether the new tests are regression, coverage expansion, or both
- prefer high-signal assertions over broad boilerplate
- treat the inability to add tests as a real coverage gap

## Stop Conditions

- the relevant behavior is covered well enough to trust the change
- the repo’s test setup is too broken or too incomplete to extend safely

## Do Not

- do not introduce a new test framework without a concrete need
- do not add low-signal tests that only lock in implementation trivia
