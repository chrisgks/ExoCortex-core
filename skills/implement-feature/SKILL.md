---
name: implement-feature
description: Use when the task is additive engineering work in an existing codebase and the builder needs a disciplined way to extend the current patterns, verify behavior, and stop before the work turns into open-ended redesign.
---

# Implement Feature

Add concrete behavior to an existing codebase without widening the task unnecessarily.

## Use This Skill When

- the task is additive rather than corrective
- the target behavior is concrete enough to implement
- the repo already has patterns or extension points to follow

## Goals

- align with existing architecture and conventions
- implement incrementally
- verify the new behavior with executable checks

## Required Inputs

1. The target behavior and done condition.
2. The relevant repo slice from `skills/repo-intake/`.
3. A verification path from `skills/verification-discovery/`.

## Workflow

1. Confirm the expected behavior from local contracts and nearby code.
2. Find the narrowest extension points that satisfy the task.
3. Implement incrementally instead of broad redesign.
4. Add or adjust tests when behavior changes are not already covered.
5. Run targeted verification, then any broader checks that materially improve confidence.

## Evidence Rules

- distinguish required feature work from optional cleanup
- cite the local code patterns that guided the implementation when relevant
- report missing test coverage or incomplete integration explicitly

## Stop Conditions

- the requested behavior is implemented and verified
- the task crosses into product scoping, research, or architectural redesign

## Do Not

- do not silently widen the feature
- do not replace established patterns without a concrete reason
