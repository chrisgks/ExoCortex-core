# System Workflows

## Session Processing

1. Capture transcript and metadata.
2. Append the full transcript into the daily raw journal.
3. Summarize the session with timestamps, confidence, and rationale.
4. Extract candidates for memory, workflows, skills, rules, intents, and self-model.
5. Promote durable items at the right level.

## Rule Revision

1. Identify repeated ambiguity or friction.
2. Check whether an existing workflow or rule already covers it.
3. If not, propose a new rule at the appropriate level.
4. Keep system-level rules minimal.

## Intent Review

1. Treat extracted intent as soft signal by default, not as durable state.
2. Review `journal/inbox/pending-intents.md` before changing `system/OPEN LOOPS.md` or `system/PRIORITIES.md`.
3. Confirm into `system/OPEN LOOPS.md` only after human review using `python3 tools/workers/intent_review.py confirm-open-loop <needle>`.
4. Promote into `system/PRIORITIES.md` only after the open loop is already confirmed and the signal has become urgent or repeated enough to justify active focus.
5. Keep rejected or already promoted items in `journal/inbox/reviewed-intents.md` so the evidence trail remains visible.
