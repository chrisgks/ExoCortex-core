# Intelligence Loop

Purpose: keep ExoCortex active, compact, and improving without turning every interaction into manual bookkeeping.

## Operating Model

You are not expected to run maintenance commands by hand during normal use.

Default behavior:

1. During a conversation, the agent should run `observe` and `stage` checks itself when they are relevant to the task.
2. When a finding maps to a safe reversible action, the agent can propose it and apply it if the user has given clear permission for that class of action.
3. When a finding maps to durable state changes, raw-file movement, irreversible edits, or external side effects, the agent must ask before acting.
4. Outside conversations, only explicitly installed automations run. Those automations should report by default, not silently rewrite durable state.

The intended user experience is: ExoCortex notices, prepares, and asks only at authority boundaries. The user should mainly make judgment calls, not remember command names.

## Authority Levels

- `observe`: read context, detect signals, write reports or summaries.
- `stage`: create review queues, seed notes, or proposed changes.
- `safe_apply`: make reversible maintenance edits with explicit command flags.
- `durable_promote`: change memory, rules, workflows, self-model, priorities, or wiki claims.
- `external_action`: email, calendar, purchases, messages, deletes, or public side effects.

Default authority: `observe` and `stage` are allowed when they directly support the current task. `safe_apply` requires an explicit apply flag or direct user instruction. `durable_promote` requires evidence plus either user confirmation or a narrow auto-apply rule already encoded in the promotion router. `external_action` always needs clear authority rules.

## Trigger Matrix

| Trigger | Action | Authority | Command |
|---|---|---|---|
| Every wrapped session ends | Summarize session, extract candidates, update queues and weekly synthesis | stage | `process_session.py` via wrapper |
| Wrapped harness token events or local usage logs update | Print sparse `[exo] cost` status lines from actual token totals and current pricing table | observe | wrapper live monitor |
| Every priced wrapped Codex, Claude Code, or Gemini CLI session ends | Append private token and dollar record, then refresh daily usage rollup | observe | `tools/workers/usage.py` via wrapper |
| Model synthesis fails | Write summary only, suppress promotion candidates, log synthesis error | observe | `tools/workers/process_session.py` |
| `surface-now.md` is non-empty | Load it at startup and handle or archive after use | observe, safe_apply | `exocortex-hygiene apply --archive-surface-now` |
| Daily or before substantial planning | Scan hygiene across active context, queues, raw inbox, wiki map, and session artifacts | observe | `exocortex-hygiene check --write-report` |
| `wiki_map_stale` finding | Refresh compact wiki discovery map | safe_apply | `exocortex-hygiene apply --refresh-wiki-map` |
| `session_artifacts_incomplete` finding | Reprocess missing session summaries and candidates in small batches | safe_apply | `exocortex-hygiene apply --reprocess-sessions --reprocess-limit 10` |
| `raw_inbox` finding | Dry-run ingestion first, then create seed source notes and move raw files when approved | stage, safe_apply | `exocortex-ingest --limit 10`, then `exocortex-ingest --apply --limit 10` |
| Pending candidates grow or age | Review, accept, reject, defer, or expire candidates | durable_promote for accept | `exocortex-review stats`, `exocortex-review list`, `exocortex-review expire --days 30 --apply` |
| Active context exceeds limits | Compact current focus, move history to wiki or journal, preserve links to evidence | durable_promote | manual edit guided by `hygiene-status.md` |
| Query cannot be answered from active preload | Search managed markdown before guessing | observe | `exocortex-retrieve "<query>"` |
| User asks whether the system works | Report operational health across loop components | observe | `exocortex-health` |
| User asks what this costs | Summarize private token/cost ledger | observe | `exocortex-usage summary today`, `exocortex-usage summary week`, `exocortex-usage summary month` |
| User repeats the same view several times | Offer the strongest opposing consideration so the decision is tested | observe | conversation rule |
| Reasoning appears one-sided or closed | Flag it plainly and continue from a truth-seeking frame | observe | conversation rule |

## Pruning Rule

Pruning means reducing active context load, not deleting evidence.

Move inactive detail from `STATE.md`, `README.md`, indexes, and other preloaded files into the appropriate wiki page, session summary, raw source note, or archive. Keep active context files focused on current decisions, blockers, live focus, and links to durable evidence.

## External Tools

The core loop must remain useful with only files, wrappers, and local Python workers. External retrieval tools such as QMD, embeddings, or a vector database are optional acceleration layers, not prerequisites.

Adopt an external retrieval backend when at least one condition is true:

- important answers regularly require searching many notes by meaning, not exact words
- `exocortex-retrieve` or `rg` finds too much noise for common queries
- cross-project synthesis depends on latent similarity across sources
- the retrieval backend can be rebuilt locally and does not become the source of truth

Until then, use deterministic loading, `wiki-map.md`, `exocortex-retrieve`, and focused file reads.

## Failure Rule

Maintenance actions must be bounded. Batch jobs that reprocess historical artifacts need a per-item timeout and a small limit. If a bounded batch fails repeatedly, stop retrying and promote the failure mode into the active maintenance plan instead of continuing in the background.

## Test Contract

Intended behaviours live in `system/INTENDED BEHAVIORS.md` because they are operational contracts, not test implementation. Scenario tests live under `tests/behavior/` and assert those contracts against fake repositories.
