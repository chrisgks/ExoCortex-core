---
title: ExoCortex system architecture
type: analysis
status: active
created: 2026-04-12
updated: 2026-04-12
summary: Filesystem-level design for turning this repo into a markdown-first external brain with static agents, hierarchical contexts, journals, workflows, and system-level control files.
source_count: 0
tags: analysis, exocortex, architecture
---

# ExoCortex system architecture

## Question

How is this repository being extended from a wiki scaffold into a broader external-brain system?

## Summary

ExoCortex is being structured as a markdown-first operating system with:

- a root control context for top-level orchestration
- static agents under `agents/`
- hierarchical domains and projects under `domains/`
- a cross-harness journal under `journal/`
- control-plane files under `system/`
- the original `raw/` and `wiki/` layers preserved as a knowledge subsystem

## Key Design Decisions

- The repo root is a real operating context rather than a passive container.
- `chief-of-staff` is the default root-level agent.
- Root-level casual conversation is first-class and defaults to `conversation` mode.
- Every serious context uses an explicit file contract built around `README.md`, `AGENT.md`, `MEMORY.md`, `STATE.md`, and `WORKFLOWS.md`.
- The managed knowledge subsystem has its own local contract at [[Operating Contract]], and `wiki/` plus `raw/` work should route to `knowledge-steward`.
- `WORKFLOWS.md` is first-class so the system can learn procedures, not just facts.
- System behavior is organized around six modes: ingestion, conversation, processing, compression, application, and synthesis.
- Context is discovered from the current folder plus contract-bearing ancestors, then filtered by agent and mode rather than injected wholesale.
- Session signal should move through explicit stages such as raw trace, candidate, repeated pattern, and trusted durable signal rather than jumping straight into memory.
- A separate health-state overlay can be summarized into sessions and used to adapt tone, scope, pacing, and question load.
- Inferred functional state should incorporate the current moment, previous-day carryover, and short recent trend rather than behaving like a single-point snapshot.

## Implications

- Future runtime tooling can treat folder location as the discovery mechanism for context.
- Durable improvements can be promoted into memory, workflows, skills, and decision rules at the appropriate level.
- The system can remain inspectable because the main contracts are markdown files rather than opaque application state.

## Runtime Status

- Initial wrappers now exist for `codex`, `claude`, and `gemini` under `tools/wrappers/bin/`.
- A generic wrapper builds a context packet from local and ancestor contracts, captures streamed user/tool sessions, and writes per-session manifests.
- The wrapper now routes `wiki/` and `raw/` paths to `knowledge-steward` by default and preloads the local operating contract for that subsystem.
- The local worker now performs heuristic structured extraction by default and can optionally use a model-backed summarizer through Claude print mode when configured.
- Daily raw journals embed the wrapper-captured session stream, while daily summaries include structured fields such as completed tasks, decisions, open questions, follow-ups, confidence, and rationale.
- Random conversation is treated as meaningful signal and may contribute to self-model, memory, workflow, and question-template candidates.
- Session manifests can now carry a separate health snapshot drawn from `system/HEALTH STATE.md`.
- Session summaries now include reflection fields such as what mattered, repeated patterns, model updates, and what should be easier next time.
- Session candidates now carry evidence metadata and are aggregated into grouped review queues plus a weighted reusable-context cache.
- ISO-week synthesis pages now consolidate repeated patterns, model updates, open questions, and review-ready candidates.
- Basic smoke tests now cover agent resolution, context assembly, prompt injection, journal idempotence, and heuristic summary extraction.
- Richer promotion logic and stronger retrieval over human notes remain future work.

## Related Pages

- [[Overview]]
- [[ExoCortex action-space model]]
- [[Coding agents]]
- [[Compound engineering]]
- [[Key patterns from Agentic Engineering Patterns]]

## Sources

- None yet. This page documents internal design decisions made in the repository.

## Open Questions

- How should wrappers and workers encode context assembly for different harnesses once project-local metadata becomes richer?
- How much of memory and workflow promotion should remain human-reviewed in v1?
