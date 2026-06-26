# Workers

Workers should eventually:

- summarize closed sessions
- extract promotion candidates
- append daily journal views
- prepare memory, workflow, skill, rule, and intent promotions
- generate grouped review queues and weekly synthesis

Current implementation:

- `process_session.py`
- `intent_review.py`
- `review.py`
- `context_hygiene.py`
- `wiki_map_maintain.py`
- `reprocess_sessions.py`
- `ingest_raw.py`
- `retrieve.py`
- `health_check.py`
- `logbook.py`
- `usage.py`

Behavior:

- reads a session manifest and streamed session log
- generates a structured summary, reflection fields, and promotion-candidate files
- writes structured candidate records with evidence metadata and signal-ladder state
- updates grouped review queues in `journal/inbox/`
- keeps inferred-intent review state in `journal/inbox/intent-review-state.json`
- promotes reviewed intent into `system/OPEN LOOPS.md` and `system/PRIORITIES.md` with evidence-backed entries
- updates weighted reusable-context cache in `journal/inbox/context-cache.json`
- updates weekly synthesis pages in `journal/weekly/`
- appends captured-session-stream entries to `journal/raw/YYYY-MM-DD.md`
- appends structured entries to `journal/summarised/YYYY-MM-DD.md`
- checks context hygiene across active context files, pending queues, raw inbox, wiki-map freshness, and session artifact completeness
- writes `journal/inbox/hygiene-status.md` when requested
- refreshes `wiki-map.md` from managed wiki indexes
- identifies and reprocesses sessions missing summary or candidate artifacts
- stages raw inbox files as wiki source notes and moves processed files into `raw/processed/YYYY-MM-DD/`
- searches managed markdown beyond active preload context when a question needs more than the startup files
- reports operational health across hygiene, session artifacts, candidate queues, raw inbox, latest session, and the Logbook
- records every durable write and file move to the append-only, reversible `journal/logbook.jsonl`
- records token usage and dollar cost for wrapped Codex, Claude Code, and Gemini CLI sessions in the private `journal/usage/` usage record using `system/USAGE RATES.json`
- refreshes the startup brief (`journal/inbox/brief.md`) so the next session opens on current state

`process_session.py` is dispatched **detached** by the Stop hook (`session_hook.py`) and the wrapper — it runs in its own process group with no controlling terminal and its output to `journal/logs/worker.log`, so the long summary/synthesis chain always runs to completion and is never killed by a hook timeout. The brief is the one piece not left to this background pass: the Stop hook renders it synchronously (it is cheap), and the session-start hook renders it fresh again on open, so the brief is always current regardless of worker timing.

By default the session worker uses model-backed extraction if available and fails closed for promotion candidates if model calls fail. Heuristic summaries can still be written, but heuristic promotion candidates are suppressed.
