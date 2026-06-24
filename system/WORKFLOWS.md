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

## Context Hygiene

1. Run `exocortex-hygiene check --write-report` to inspect the whole ExoCortex maintenance surface.
2. Treat active `STATE.md` files as working memory, not archives.
3. Keep current focus, live blockers, unresolved questions, and active decisions in `STATE.md`.
4. Move completed, superseded, stale, or historical material into the appropriate journal, wiki log, or archive surface.
5. Preserve source truth and decision history. Pruning means compacting active context, not deleting evidence.
6. Warn when active preload files become large enough that context truncation could hide decisive information.
7. Use explicit apply flags for mutations, such as `exocortex-hygiene apply --archive-surface-now`.

## Self-Maintenance Cadence

1. After every wrapped session, run session processing to create summaries, candidates, review queues, context cache, and weekly synthesis.
2. Daily, run `exocortex-hygiene check --write-report` to surface stale context, oversized active files, queue growth, raw inbox backlog, wiki-map drift, and incomplete session artifacts.
3. Weekly, run `exocortex-review stats` and review the highest-signal candidates before changing durable memory, rules, workflows, or self-model files.
4. When `raw/inbox/` has files, route to knowledge-steward ingestion rather than leaving source material unclassified.
5. When wiki files change, refresh `wiki-map.md` before relying on cross-wiki discovery.
6. When hygiene findings stay unresolved across multiple reports, promote the maintenance issue into `system/OPEN LOOPS.md` or the nearest project `STATE.md`.
7. Use `exocortex-hygiene apply --refresh-wiki-map` for wiki-map drift and `exocortex-hygiene apply --reprocess-sessions --reprocess-limit 10` for missing session artifacts.
8. Dry-run raw ingestion with `exocortex-ingest --limit 10` before applying `exocortex-hygiene apply --ingest-raw --ingest-limit 10`.
9. Use `exocortex-retrieve "<query>"` before guessing when active preload does not contain enough context.
10. Use `exocortex-review defer <needle>` for candidates that need more evidence and `exocortex-review expire --days 30 --apply` for stale low-value candidates.
