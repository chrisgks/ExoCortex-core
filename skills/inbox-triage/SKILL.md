---
name: inbox-triage
description: Use when files have been dropped into an inbox directory and need to be classified, renamed, moved into the correct pipeline, and summarized back to the user. This skill is for pre-ingestion triage, not knowledge-base ingestion: categorize first, then decide whether the destination is a working project, durable source storage, a scoped knowledge base, or continued holding in the inbox.
---

# Inbox Triage

Triage the inbox directory before any knowledge-base ingestion.

## Goals

- classify each new inbox file by function, not by topic
- move it to the narrowest sensible destination
- use human-readable filenames
- avoid promoting unread or weakly understood material into durable knowledge too early
- tell the user what changed and what, if anything, needs a decision

## Classification Rules

Use these buckets first (substitute your own paths for the `<destination>` placeholders):

- `<writing-candidates>/`
  - for theses, arguments, interpretive frames, and early writing candidates
- `<idea-holding>/`
  - for interesting ideas that may later become writing, memory, or knowledge-base material
- `<design-notes>/`
  - for internal architecture notes, system models, and design memos that still need reconciliation
- `<durable-sources>/`
  - for external source material that should become durable source input
- leave in the inbox
  - only if classification is genuinely unclear

Do not move something into the knowledge base just because it is interesting. Knowledge-base placement requires later synthesis and usually prior reading or reconciliation.

## Triage Heuristics

Ask, in order:

1. Is this mainly a writing candidate?
2. Is this mainly a source?
3. Is this mainly an early idea?
4. Is this mainly a design note?
5. Is it still too ambiguous to place safely?

Use these tests:

- **Function test**: what is this file for?
- **Readiness test**: is this raw input or already durable synthesis?
- **Ownership test**: does it belong to writing, source storage, or pre-ingestion holding?
- **Promotion test**: would moving it upward create fake certainty?

Default conservative choices:

- summary of someone else's article without the original source -> writing candidate or idea holding, not durable knowledge
- interpretation without direct grounding -> writing candidate or idea holding, not the knowledge base
- system architecture reflection that may conflict with current design docs -> design note first

## Required Actions

When using this skill:

1. Inspect all uncategorized files in the inbox directory.
2. Read each candidate file before moving it.
3. Rename files into human-readable names when useful.
4. Move files into the correct bucket.
5. If a writing candidate becomes active, update the relevant writing project state.
6. If the triage reveals a missing destination pipeline, create the smallest necessary structure to support it.
7. Report the result to the user:
   - what moved where
   - what remains ambiguous
   - what should happen next

## Do Not

- do not rewrite the contents of raw files unless explicitly asked
- do not ingest directly into the knowledge base during triage
- do not pretend a summary is equivalent to a checked source
- do not create lots of new buckets without clear need

## Future Automation Boundary

This skill standardizes the triage behavior, but it does not by itself create passive automation.

For true automatic pickup and notification, you need a watcher or wrapper hook that:

1. detects new files in the inbox directory
2. invokes this skill or an equivalent triage routine
3. returns a notification or writes a triage report

Until then, use this skill whenever the user asks to process inbox material.
