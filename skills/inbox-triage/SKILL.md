---
name: inbox-triage
description: Use when files have been dropped into raw/inbox/ and need to be classified, renamed, moved into the correct pipeline, and summarized back to the user. This skill is for pre-ingestion triage, not wiki ingestion: categorize first, then decide whether the destination is a writing project, raw/sources, a scoped wiki, or continued holding in raw/inbox.
---

# Inbox Triage

Triage `raw/inbox/` before any wiki ingestion.

## Goals

- classify each new inbox file by function, not by topic
- move it to the narrowest sensible destination
- use human-readable filenames
- avoid promoting unread or weakly understood material into wiki knowledge too early
- tell the user what changed and what, if anything, needs a decision

## Classification Rules

Use these buckets first:

- `domains/writing/projects/essays/seeds/`
  - for essayable theses, cultural arguments, interpretive frames, and early writing candidates
- `raw/inbox/concept_seeds/`
  - for interesting ideas that may later become writing, memory, or wiki material
- `raw/inbox/design_notes/`
  - for internal architecture notes, system models, and design memos that still need reconciliation
- `raw/sources/`
  - for external source material that should become durable source input
- leave in `raw/inbox/`
  - only if classification is genuinely unclear

Do not move something into a wiki just because it is interesting. Wiki placement requires later synthesis and usually prior reading or reconciliation.

## Triage Heuristics

Ask, in order:

1. Is this mainly an essay seed?
2. Is this mainly a source?
3. Is this mainly a concept seed?
4. Is this mainly a design note?
5. Is it still too ambiguous to place safely?

Use these tests:

- **Function test**: what is this file for?
- **Readiness test**: is this raw input or already durable synthesis?
- **Ownership test**: does it belong to writing, source storage, or pre-ingestion holding?
- **Promotion test**: would moving it upward create fake certainty?

Default conservative choices:

- summary of someone else's article without the original source -> essay seed or concept seed, not wiki knowledge
- philosophy interpretation without direct grounding -> essay seed or concept seed, not concept wiki
- system architecture reflection that may conflict with live ExoCortex docs -> design note first

## Required Actions

When using this skill:

1. Inspect all uncategorized files in `raw/inbox/`.
2. Read each candidate file before moving it.
3. Rename files into human-readable names when useful.
4. Move files into the correct bucket.
5. If an essay seed becomes active, update the relevant writing project state.
6. If the triage reveals a missing destination pipeline, create the smallest necessary structure to support it.
7. Report the result to the user:
   - what moved where
   - what remains ambiguous
   - what should happen next

## Do Not

- do not rewrite the contents of raw files unless explicitly asked
- do not ingest directly into a wiki during triage
- do not pretend a summary is equivalent to a checked source
- do not create lots of new buckets without clear need

## Future Automation Boundary

This skill standardizes the triage behavior, but it does not by itself create passive automation.

For true automatic pickup and notification, ExoCortex needs a watcher or wrapper hook that:

1. detects new files in `raw/inbox/`
2. invokes this skill or an equivalent triage routine
3. returns a notification or writes a triage report

Until then, use this skill whenever the user asks to process inbox material.
