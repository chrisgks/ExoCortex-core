---
name: code-review
description: Use when the user wants a thorough code review, release-readiness audit, bug hunt, or architecture risk assessment of a codebase. This skill is for evidence-backed review of real behavior, tests, contracts, prompts, and operational flows; it prioritizes correctness, risk, and coverage gaps over style commentary.
---

# Code Review

Run an adversarial, findings-first review of a target codebase.

## Use This Skill When

- the user asks for a code review
- the user wants a release-readiness audit
- the task is to find bugs, regressions, or risk
- the repository has multiple surfaces that must be checked together
- the user wants severity-ranked findings with file evidence

## Review Standard

- prioritize correctness, behavior, and operational risk over style
- inspect end-to-end flows, not isolated files
- treat tests, prompts, docs, config, wrappers, scripts, and filesystem behavior as part of the product surface when they affect runtime behavior or operator expectations
- prefer a smaller set of confirmed findings over a long list of weak suspicions
- downgrade unsupported claims into open questions

## Required Inputs

At the start of a review:

1. Identify the target root.
2. Read the top-level contract files first.
3. Read local review guidance if the target includes `REVIEW.md`.
4. Map the major subsystems and rank them by risk before diving deep.

## Workflow

1. Read the project contract:
   - top-level `README.md`
   - any repo policy files such as `AGENTS.md`, `WORKFLOWS.md`, or local agent/context files
   - `REVIEW.md` if present
2. Build a subsystem map:
   - runtime code
   - tests
   - automation scripts
   - prompts/config/contracts
   - UI/backend surfaces if present
3. Start with the highest-risk subsystem.
4. Trace one or more end-to-end flows through that subsystem.
5. Try to falsify intended behavior using code, tests, and direct checks.
6. Run relevant validation steps when available:
   - tests
   - lint
   - build
   - targeted commands that exercise the reviewed path
7. Record confirmed issues with severity, file references, failure mode, and fix direction.
8. Record unresolved but material uncertainty as an open question or coverage gap.
9. Continue until the review goal is met or the remaining uncertainty is explicitly documented.

## Evidence Rules

- every finding needs file references and line numbers
- every severity claim should match the likely impact
- separate confirmed issues from hypotheses
- if verification was attempted and failed, say exactly what was run and what blocked it
- if an area was not reviewed deeply, name it under coverage gaps

## Output Shape

Return results in this order:

1. Findings
2. Open Questions
3. Coverage Gaps
4. Short Overall Assessment

Findings should be ordered by severity: Critical, High, Medium, Low.

For each finding include:

- title
- severity
- file reference(s) with line numbers
- what is wrong
- why it matters
- recommended fix direction
- missing test(s), if any

## Local Adaptation Rule

If the target repository contains `REVIEW.md`, treat it as the project-specific review contract. Use it to adapt subsystem priorities, review invariants, and done criteria. Do not let it lower the evidence standard of this skill.

## Do Not

- do not lead with praise or summary
- do not pad the review with style nits
- do not claim confidence you did not earn
- do not treat documentation mismatches as harmless when docs define expected behavior
- do not stop after one pass if major high-risk areas remain unchecked
