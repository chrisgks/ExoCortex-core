---
name: verification-discovery
description: Use when builder needs to determine the cheapest trustworthy way to verify a change in the current repo before or during implementation.
---

# Verification Discovery

Find the narrowest trustworthy checks for the target behavior.

## Use This Skill When

- the task is concrete but the verification path is unclear
- the repo has multiple possible test, lint, or build commands
- the builder should not start editing until it knows how success will be judged

## Goals

- identify the fastest trustworthy checks for the changed path
- separate strong verification from weak heuristics
- make the done condition executable when possible

## Required Inputs

1. The target behavior, bug, or feature area.
2. Repo markers and likely commands from `skills/repo-intake/`.
3. Any existing test files or CI commands related to the path.

## Workflow

1. Look for project-local scripts, make targets, test config, and CI commands.
2. Rank checks from narrowest to broadest.
3. Prefer targeted tests or direct reproductions first.
4. Keep broader checks ready for final confirmation when they add real confidence.
5. If no trustworthy verification exists, route to `skills/add-tests/` or surface a blocker.

## Evidence Rules

- say which commands are confirmed by repo files versus inferred from conventions
- prefer executable checks over manual confidence statements
- treat missing verification as a real risk, not a footnote

## Stop Conditions

- a reasonable verification path is identified
- the repo cannot support trustworthy verification without additional test work or setup

## Do Not

- do not treat “it looks right” as sufficient verification
- do not choose the biggest command by default if a smaller one proves the same thing
