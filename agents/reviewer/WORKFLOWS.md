# Reviewer Workflows

## Full Project Review

1. Read the target project's contract files first.
2. Load `skills/code-review/SKILL.md` as the default review workflow.
3. Read local `REVIEW.md` if the target project provides one.
4. Use `tools/prompts/full_repo_code_review.md` only as a launcher prompt or handoff brief when needed.
5. Map the major subsystems and rank them by risk.
6. Inspect code, tests, prompts, docs, wrappers, and automations together rather than in isolation.
7. Run relevant tests, builds, lint, or direct verification commands where available.
8. Record findings with severity, file references, failure mode, and fix direction.
9. Re-run targeted review on any subsystem whose behavior changed or whose initial assessment was uncertain.
10. Stop only when the review goal is met or the remaining uncertainty is explicitly documented as a blocker or coverage gap.

## Iterative Review Loop

1. Select the highest-risk unchecked subsystem.
2. Trace one or more end-to-end flows through that subsystem.
3. Try to falsify the intended behavior using code, tests, and contracts.
4. Convert confirmed failures into findings.
5. Convert unresolved but material uncertainty into open questions or coverage gaps.
6. Continue until there are no unchecked high-risk areas for the current scope.

## Review Handoff

1. Present findings first, ordered by severity.
2. Keep open questions separate from confirmed issues.
3. State what was verified, what was not verified, and why.
4. Give a short release-readiness assessment last.
