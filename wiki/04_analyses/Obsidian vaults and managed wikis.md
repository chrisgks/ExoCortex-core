---
title: Obsidian vaults and managed wikis
type: analysis
status: active
created: 2026-04-12
updated: 2026-04-12
summary: Concrete policy for where managed wikis belong in ExoCortex, how knowledge is promoted between them, and how the root wiki now links to narrower child wikis.
source_count: 0
tags: analysis, obsidian, wiki, architecture, policy
---

# Obsidian vaults and managed wikis

## Question

What role should human-created Obsidian vaults and LLM-managed wikis play in ExoCortex, and how should they relate?

## Summary

The clean pattern is:

- the repository is the broad markdown vault
- each managed wiki is an agent-owned synthesis layer inside a specific context
- the default context for a managed wiki is a serious project, not an agent role
- knowledge stays local by default and is promoted upward only after it has demonstrated reuse outside its original context

In ExoCortex today, the repository already works as an Obsidian-compatible markdown vault, and the existing root `wiki/` is the **root wiki** for ExoCortex-wide knowledge. Topic-specific corpora should move into narrower project or domain wikis when they no longer belong at root scope.

## Decision

Wikis are agent-maintained only. Humans may read them, but should not be the routine editors of `wiki/` content.

The right abstraction is a **context boundary**, not an **agent boundary**.

- Agents are execution roles and lenses.
- Wikis are knowledge stores and synthesis layers.
- A stable role such as `research` or `knowledge-steward` may work across many contexts, so giving each agent its own default wiki would blur retrieval boundaries and create duplicated or conflicting summaries.

## Topology

Use this hierarchy by default:

- **Root wiki**: ExoCortex-wide operating knowledge, architecture, shared concepts, cross-domain patterns, and policy.
- **Domain wiki**: optional, only when multiple projects in the same domain need shared synthesis.
- **Project wiki**: the default wiki unit for serious work. This is where most source notes, analyses, entities, concepts, and contradictions should live.
- **Agent folders**: no default wiki. Keep role contracts, memory, workflows, and decision rules there instead.

Not every context should have a managed wiki. Create one only when there is enough source material, repeated synthesis, or cross-linking pressure to justify maintenance.

## Recommended Relationship

- Humans shape the broad vault structure and choose the context boundaries.
- Agents maintain the wiki layers inside those contexts.
- The human workspace can contain notes, drafts, and raw material that are not part of any managed wiki.
- Managed wikis should remain predictable enough that future ExoCortex retrieval can treat them as machine-readable synthesis, not just loose markdown.

## Practical Pattern

For a serious project context:

- human-authored or user-owned files:
  - `README.md`
  - ad hoc notes
  - source material
  - artifacts
- agent-maintained operational files:
  - `AGENT.md`
  - `MEMORY.md`
  - `STATE.md`
  - `WORKFLOWS.md`
  - `DECISION RULES.md`
  - optional local `wiki/`
  - optional local `journal/`

That makes the project folder a human-readable workcell with an optional agent-maintained synthesis layer.

## When To Create A Wiki

Create a local wiki when:

- the context has several sources or recurring conversations to synthesize
- entities, concepts, or decisions need durable cross-linking
- future sessions are likely to benefit from retrieval over distilled knowledge rather than raw notes alone

Do not create one when:

- the context is short-lived
- `README.md`, `AGENT.md`, `MEMORY.md`, `STATE.md`, and `WORKFLOWS.md` are enough
- the knowledge is mostly execution state rather than durable synthesis

## Promotion Policy

Knowledge should stay at the narrowest valid scope by default.

- Keep it in the **project wiki** when it depends on local stakeholders, repo shape, deadlines, or temporary implementation facts.
- Promote it to the **domain wiki** when it helps more than one project in the same domain.
- Promote it to the **root wiki** when it is reusable across domains, agents, or the ExoCortex runtime itself.

Use these tests before promoting knowledge upward:

- **Abstraction test**: can the claim be stated without project-specific nouns?
- **Transfer test**: would another context make a better decision because of it?
- **Recurrence test**: has the pattern appeared more than once?
- **Stability test**: is it likely to remain true for a while?
- **Retrieval test**: would future ExoCortex reasonably search for this from outside the original context?

If the answer is weak or unclear, do not promote. Leave the knowledge local and wait for stronger evidence.

## Machine-Readable Contract

Each managed wiki should expose enough local metadata that future ExoCortex retrieval can reason about scope.

At minimum, each wiki should have:

- `index.md`
- `log.md`
- `00_meta/`
- explicit frontmatter on substantive pages

Add a wiki-local scope page or equivalent metadata that declares:

- `context_path`
- `scope`: `root|domain|project`
- `owner_agent`
- `parent_wiki`
- `child_wikis`
- `promotion_rule`

Page frontmatter should remain strict and should eventually gain fields such as:

- `scope`
- `reusability`
- `promotion_status`
- `promotion_evidence`

## Current Integration

Current integration is now partly structural and partly explicit:

- all content is markdown and Obsidian-compatible
- wrappers discover context from the filesystem hierarchy
- project folders are designed to host both human files and optional wiki subfolders
- the retrieval and promotion rules above are now the intended contract
- the first child wiki now lives at [[domains/learning/projects/agentic-engineering/wiki/index|Agentic Engineering project wiki]]

There is not yet a dedicated runtime that indexes arbitrary vault notes separately from the managed wiki layer. Stronger retrieval and scope enforcement remain future work, but the first local wiki boundary is now real rather than only planned.

## Retrieval Order

Future ExoCortex retrieval should prefer the narrowest useful scope:

1. current project wiki
2. current domain wiki
3. root wiki
4. raw sources and human-authored notes

This preserves locality and reduces the risk that broad generalizations overwrite local truth.

## Current Repo State

The first root-to-project wiki split is now in place:

- the root wiki holds ExoCortex-wide architecture, policy, and operating knowledge
- the Agentic Engineering project wiki holds the narrower learning corpus that used to live at root scope

That split should now be treated as the reference pattern for future managed wikis.

## Migration Pattern

When a root or domain wiki accumulates a topic corpus that no longer belongs at that scope:

1. Keep root-scope pages in the root wiki.
   - Examples: `[[ExoCortex system architecture]]`, `[[ExoCortex implementation review]]`, `[[Compounding signal design for ExoCortex]]`, `[[Using exported assistant memory]]`, and this page.
2. Create or confirm the local wiki that owns the narrower corpus.
   - Current example: [[domains/learning/projects/agentic-engineering/wiki/index|Agentic Engineering project wiki]] under `domains/learning/projects/`.
3. Move topic-specific source notes, concept pages, and overview pages into that narrower wiki.
   - Examples: `[[Agentic Engineering Patterns Guide]]`, `[[Simon Willison]]`, `[[Agentic engineering]]`, `[[Coding agents]]`, `[[Agentic testing]]`, `[[Subagents]]`, `[[Cognitive debt]]`, `[[Compound engineering]]`, and the related source notes.
4. Rebuild the root wiki index so it points only to root-scope knowledge plus links outward to child wikis.
5. Treat the root wiki as a directory of durable ExoCortex knowledge, not as the default dumping ground for every interesting corpus.

The migration should be conservative. Do not silently discard existing links or claims. Preserve redirects, relationship notes, or explicit migration records when content moves.

## Implications

- ExoCortex should treat managed wikis as a scoped memory hierarchy rather than one global pool.
- The project wiki is the default place to accumulate knowledge.
- Domain and root wikis should remain sparse, curated, and more reusable.
- The root wiki should continue behaving like a map of ExoCortex-wide synthesis and child wikis rather than reverting into a mixed starter notebook.

## Related Pages

- [[ExoCortex system architecture]]
- [[Overview]]
- [[Compound engineering]]
- [[Key patterns from Agentic Engineering Patterns]]

## Sources

- None yet. This page records internal architectural decisions for ExoCortex.

## Open Questions

- Should local wiki metadata live in a dedicated `00_meta/Scope.md` page, in `index.md` frontmatter, or both?
- How should future retrieval distinguish between human notes, agent-maintained wiki synthesis, and journal-derived reusable context?
