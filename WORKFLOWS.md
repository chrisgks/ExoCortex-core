# Root Workflows

## Best Next Action

1. Read root `STATE.md`, `system/PRIORITIES.md`, and `system/OPEN LOOPS.md`.
2. Read the relevant domain or project `STATE.md`.
3. Identify the highest-leverage unresolved action.
4. Produce one concrete next step with a done condition.
5. Route execution to the right agent if needed.

## Project Intake

1. Place the work in the correct domain.
2. Scaffold the project contract.
3. Record goal, constraints, and open questions in `STATE.md`.
4. Create or point to the relevant wiki and artifact location.
5. Route the next step to planning, research, or builder.

## Promotion Review

1. Review recent session summaries.
2. Identify repeated facts, workflows, skills, and decision rules.
3. Promote them at the lowest level that explains the repetition.
4. Escalate to root or system only when patterns are cross-domain.

## Code Review

1. Start from the target project root.
2. Read the target `README.md` and local `REVIEW.md` if present.
3. Route execution to the `reviewer` agent.
4. Apply the `skills/code-review/` workflow.
5. Keep reviewing until the remaining uncertainty is explicitly captured as open questions or coverage gaps.

## Intent Review

1. Read `journal/inbox/pending-intents.md`.
2. Use `python3 tools/workers/intent_review.py list` to see pending inferred intents and reviewed outcomes together.
3. Confirm an item into `system/OPEN LOOPS.md` only when it is repeated, high-confidence, or clearly phrased as a commitment.
4. Promote it into `system/PRIORITIES.md` only after it is already an open loop and has become urgent or repeatedly evidenced.
5. Reject one-off or weak inferences with `python3 tools/workers/intent_review.py reject <needle>` so they stop resurfacing without losing the decision trail.

## Inbox Triage

1. Read the uncategorized files in `raw/inbox/`.
2. Use the `inbox-triage` skill to classify them by function before ingestion.
3. Move each file into the narrowest sensible destination.
4. Rename files into human-readable names when useful.
5. Report the resulting moves and any remaining ambiguity to the user.
