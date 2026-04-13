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

By default the worker uses heuristic extraction. If `EXOCORTEX_SUMMARIZER_PROVIDER=claude` is set and `EXOCORTEX_REAL_CLAUDE` points to the underlying Claude binary, it will attempt model-backed semantic summarization and fall back to heuristics on failure.
