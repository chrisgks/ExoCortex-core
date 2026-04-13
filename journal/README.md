# Journal

The journal is the chronological record of cross-harness activity.

## Structure

- `raw/`: daily raw journal views
- `summarised/`: daily summary views
- `sessions/`: canonical per-session records
- `inbox/`: pending promotion candidates
- `weekly/`: ISO-week synthesis pages

## Rule

All sessions may generate signal. Only curated signal should update durable memory, workflows, skills, persona calibration, or self-model.

## Capture Contract

- `raw/YYYY-MM-DD.md` should capture the full daily ledger, including session metadata and the wrapper-captured session stream.
- `summarised/YYYY-MM-DD.md` should capture structured summaries including completed tasks, decisions, open questions, follow-ups, timestamps, health overlay, confidence, and rationale.
- Session summaries may also surface inferred future-intent signals when the transcript suggests likely plans or commitments that are softer than explicit tasks.
- Canonical per-session files remain the source of truth for replay and retries.

The session stream is intended to include both forwarded user input and tool output when the session is launched through the Exocortex wrapper.

## Compounding Loop

- Each session may produce structured candidates with evidence metadata and signal-ladder state.
- `journal/inbox/` stores grouped review queues plus a weighted reusable-context cache for future sessions.
- `journal/inbox/pending-intents.md` is the human review queue for inferred future-intent signals that have not yet become durable state.
- `journal/inbox/reviewed-intents.md` records confirmed, rejected, and priority-promoted intent outcomes without erasing their evidence.
- `journal/inbox/intent-review-state.json` is the machine-readable ledger that keeps those review decisions from reappearing as pending.
- `journal/weekly/` stores periodic synthesis so repeated themes do not remain trapped in daily logs.

## Health Overlay

- Every session may include a summarized health snapshot from `system/HEALTH STATE.md`.
- Health state should be logged separately from transcript content so it can be analyzed later.
- Health state is operational context, not diagnosis.
