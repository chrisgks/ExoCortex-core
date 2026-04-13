---
title: Operating Contract
type: meta
status: active
created: 2026-04-12
updated: 2026-04-12
summary: Authoritative contract for managed wiki maintenance and raw-source handling in ExoCortex.
source_count: 0
tags: meta, operating-contract, wiki, raw
---

# Operating Contract

This page defines the operating contract for work that touches any managed `wiki/` plus `raw/`.

Use it together with [[Scope]] and [[Maintenance]]. The default owner for this layer is `knowledge-steward`.

## Mission

Maintain persistent markdown knowledge layers that sit between the user and the raw source corpus.

- `raw/` is the source of truth.
- each managed `wiki/` is an agent-maintained synthesis layer for its context
- the root `wiki/` is the root managed wiki
- this operating contract defines the wiki-layer bookkeeping rules

The human curates sources and asks questions. The agent performs the bookkeeping: summarize, synthesize, cross-link, update, reconcile, and log.

For repo-wide runtime behavior, `README.md` and `AGENT.md` are the broader runtime contracts. For wiki maintenance, this page is authoritative.

This vault is intentionally markdown-only. Do not rely on helper scripts or external indexing tools as part of the default workflow. Read files, edit files, and keep the wiki navigable through the markdown itself.

## Non-Negotiable Rules

1. Treat `raw/` as immutable content. Read from it, cite it, register it, but do not rewrite its contents unless the user explicitly asks for reorganization work.
2. Treat every managed `wiki/` as agent-owned. Humans may read them, but agents maintain them.
3. Before substantial wiki work, read the nearest relevant `wiki/index.md` and any obviously relevant pages.
4. After substantial wiki work, update that wiki's `index.md` and append an entry to that wiki's `log.md`.
5. If the work changes root-scope understanding, child-wiki discoverability, or cross-wiki policy, also update the root `wiki/index.md` and root `wiki/log.md`.
6. Prefer editing an existing page over creating a redundant new page.
7. Use Obsidian-style wikilinks such as `[[Overview]]`, `[[Memex]]`, and `[[Source - 2026-04-11 - Example Article]]`.
8. In a multi-wiki vault, use path-qualified wikilinks when page-name collisions would otherwise be ambiguous.
9. Do not invent citations, sources, quotes, or certainty. If something is unclear, label it as uncertain.
10. If new evidence conflicts with older synthesis, record the conflict explicitly instead of silently overwriting the old claim.

## Directory Contract

### Raw Sources

- `raw/inbox/`: newly dropped files that may not be registered yet
- `raw/sources/`: curated raw source files
- `raw/assets/`: images or attachments associated with sources

### Managed Wikis

Each managed wiki should follow the same local directory contract:

- `wiki/index.md`: main content index and navigation surface
- `wiki/log.md`: append-only chronological record
- `wiki/00_meta/`: maintenance pages, scope notes, backlog, operating notes
- `wiki/01_overviews/`: broad synthesis and start-here pages
- `wiki/02_entities/`: people, organizations, places, projects, books, tools
- `wiki/03_concepts/`: ideas, methods, frameworks, themes
- `wiki/04_analyses/`: durable answers, comparisons, memos, reports
- `wiki/05_sources/`: source notes tied to raw sources
- `wiki/99_templates/`: reference templates, never treated as content pages

The root `wiki/` is only one instance of this contract.

## Page Standard

Every substantive wiki page should have frontmatter with at least:

```yaml
---
title: Page Title
type: overview|entity|concept|analysis|source|meta
status: seed|active|superseded
created: YYYY-MM-DD
updated: YYYY-MM-DD
summary: One-line summary for index generation.
source_count: 0
tags:
---
```

Minimum page quality:

- A clear one-line summary in frontmatter
- Obsidian wikilinks to related pages where appropriate
- A `## Sources` section for synthesized pages
- A `## Open Questions` or `## Gaps` section when uncertainty remains

## Filename And Page Conventions

- Use human-readable filenames with spaces.
- The page title should normally match the filename.
- Source notes should usually be named `Source - YYYY-MM-DD - Title`.
- Keep each `wiki/index.md` hand-maintained by the LLM as a compact navigation page, not a dump of every detail.
- Keep each `wiki/log.md` append-only and chronological.

## Standard Workflows

### Ingest

When asked to ingest a source:

1. Identify the raw file in `raw/`.
2. Choose the narrowest relevant managed wiki.
3. If there is no source note yet, create one under that wiki's `05_sources/`.
4. Read the raw source and the source note.
5. Update the source note with a faithful summary, key claims, entities, concepts, contradictions, and open questions.
6. Update all relevant overview, entity, concept, and analysis pages in that wiki.
7. If important concepts or entities do not yet exist, create them in that wiki.
8. Update that wiki's `index.md` so the new or changed pages are discoverable.
9. Append a log entry to that wiki's `log.md` describing what changed.
10. If you notice structural problems while ingesting, add follow-up items to that wiki's `00_meta/Backlog.md` or the root backlog if the issue is cross-wiki.

### Query

When asked a question:

1. Read the nearest relevant `wiki/index.md` first.
2. Read the most relevant pages in that wiki, then move outward to parent or child wikis only when needed, plus any directly relevant raw sources.
3. Answer from the maintained wiki plus any directly consulted sources.
4. Cite the relevant wiki pages and source notes in the answer.
5. If the answer is durable and likely to matter later, file it as a new or updated page in the narrowest valid wiki's `04_analyses/`.
6. If you create or substantially revise a durable page, update that wiki's index and log the operation.

### Lint

When asked to health-check the wiki:

1. Read the target wiki's `index.md`, `log.md`, and the pages most likely to expose structural issues.
2. Inspect for:
   - broken links
   - duplicate or overlapping pages
   - orphan pages
   - stale summaries
   - contradictory claims across pages
   - claims that need newer evidence
   - missing concept or entity pages for heavily referenced topics
3. Record actionable follow-ups in that wiki's `00_meta/Backlog.md`.
4. Log the lint pass in that wiki's `log.md`.

## Editing Heuristics

- Preserve chronology on source pages. Preserve synthesis on overview and analysis pages.
- Use concise prose. Dense and factual beats chatty.
- When a page becomes too broad, split it into narrower pages and add links both ways.
- Prefer explicit sections such as `## Contradictions`, `## Timeline`, `## Relationships`, `## Sources`, and `## Open Questions` over unstructured notes.
- Do not delete superseded claims without preserving the fact that the wiki previously held them; mark them as revised or superseded instead.
- Keep filenames human-readable. Spaces are acceptable.
- Keep each `wiki/index.md` short enough that it remains useful as the first file to read during navigation.

## Done Criteria

An operation is not complete until:

- the relevant wiki pages have been updated
- the index reflects the new page set
- the log records the work
- contradictions and uncertainties are surfaced rather than hidden

## Related Pages

- [[Scope]]
- [[Maintenance]]
- [[Index]]

## Open Questions

- None currently.
