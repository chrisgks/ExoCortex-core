# Intended Behaviors

This file defines behaviour-level contracts for the ExoCortex intelligence loop. Tests assert these contracts from `tests/behavior/`; this file is not test machinery.

## Maintenance Authority

- Observe and stage actions may run when relevant to the current task.
- Apply actions must be bounded, logged, and tied to an authority level.
- Raw-file movement, durable memory changes, wiki claim promotion, and external actions require clear authority.
- If a batch job times out or repeatedly fails, stop retrying and surface the failure mode.

## Raw Ingestion

- Dry-run ingestion lists source and destination paths without moving files.
- Apply ingestion creates seed wiki source notes, updates the relevant wiki index, moves raw files to `raw/processed/YYYY-MM-DD/`, refreshes `wiki-map.md`, and writes the maintenance ledger.
- Raw source notes must point to the processed raw file, not the old inbox path.

## Context Hygiene

- Hygiene checks report oversized active context, stale or missing focus, raw inbox backlog, wiki-map drift, pending queues, and incomplete session artifacts.
- Hygiene checks do not delete source truth.
- Active-context pruning means moving history into wiki, journal, or archive surfaces while preserving evidence.

## Session Reprocessing

- Historical reprocessing uses a small limit and a per-manifest timeout.
- A timed-out manifest returns a bounded failure code rather than hanging the maintenance loop.
- Failed reprocessing is treated as an investigation item, not as an infinite retry loop.

## Retrieval

- If active preload is insufficient, search managed markdown before guessing.
- Retrieval ignores journal transcripts by default because raw logs swamp curated knowledge.
- Journal search must be explicit.

## Review And Promotion

- Pending candidates remain soft signal until accepted, rejected, deferred, expired, or auto-applied under a narrow encoded rule.
- Durable memory, rules, workflows, self-model, and priority changes require review unless the promotion router has high-confidence low-risk evidence.
- Review decisions are append-only and auditable.

## Conversation Behaviour

- If the user repeats the same view several times, offer the strongest opposing consideration so the decision is tested.
- If reasoning appears one-sided or closed, flag it plainly.
- If confidence is low, say so.
