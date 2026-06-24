---
title: Memory Architecture
type: meta
status: active
created: 2026-05-07
updated: 2026-05-07
summary: Contract defining how claude-mem and ExoCortex split responsibility for capturing, compressing, and curating session memory. Names the per-tool capture-strategy abstraction so the wrapper does not assume a Claude-shaped world.
source_count: 0
tags: meta, memory, architecture, claude-mem, wrapper
---

# Memory Architecture

This page is the authoritative contract for how session memory is captured, compressed, and curated across the two systems that touch it: **claude-mem** (an external plugin) and **ExoCortex** (this repo). It also names the **capture-strategy abstraction** that makes the wrapper tool-agnostic.

Use it together with [[Operating Contract]] and the runtime contracts under `system/` and `tools/wrappers/`.

## The Boundary in One Sentence

**claude-mem captures and compresses raw session content. ExoCortex curates the compressed observations into the durable journal/wiki and uses them to drive agent behavior.**

If you are about to write code that does both, stop and ask which side it belongs on.

## Layer Responsibilities

### claude-mem (external)

- **Owns**: raw session capture, LLM-based compression of transcripts into observations, semantic search across sessions, cross-project memory recall.
- **Source of truth**: Claude Code's native session `.jsonl` files under `~/.claude/projects/<slug>/<session>.jsonl`.
- **Storage**: claude-mem's own SQLite/vector DB (outside this repo).
- **Hooks**: runs on Claude Code's `SessionEnd` to compress the transcript.
- **Scope**: Claude Code sessions only. Does not capture `codex` or other CLIs.

### ExoCortex (this repo)

- **Owns**: in-repo journal (`journal/`), managed wikis (`wiki/`), agent state (`system/`), bootstrap context manifest, health and intent tracking, promotion of memory into durable notes.
- **Source of truth**: the markdown files in this repo. `git` is the audit log.
- **Storage**: plain markdown + a small set of JSON state files. Everything is git-tracked.
- **Hooks**: pre/post session via the wrapper at `tools/wrappers/exocortex_wrapper.py` and post-session workers under `tools/workers/`.
- **Scope**: any wrapped CLI (currently `claude`, `codex`; pluggable for future tools).

### Interface

ExoCortex **consumes** claude-mem's compressed observations where available. It does **not** independently re-compress what claude-mem already compressed. When claude-mem is not the capture source (e.g. `codex`), ExoCortex uses its own per-tool capture strategy (see below).

## Capture-Strategy Abstraction

The wrapper does not assume a Claude-shaped world. Capture is pluggable per tool:

```
CaptureStrategy (interface)
 ├── PTYTeeStrategy          ← default fallback for any CLI
 ├── ClaudeJsonlStrategy     ← reads ~/.claude/projects/.../*.jsonl post-session,
 │                             prefers claude-mem observations when available
 └── CodexSessionStrategy    ← reads ~/.codex/sessions/.../*.jsonl post-session
```

Selection rule: the wrapper picks a strategy based on the `tool` argument. Unknown tools fall back to `PTYTeeStrategy` (current behavior, no regression).

Each strategy answers two questions:

1. **Where does the canonical transcript live?** (a file path, a DB cursor, etc.)
2. **How does ExoCortex turn that into journal/wiki entries?** (the post-session worker contract).

A new tool integration is "add a strategy", not "modify the wrapper".

## Data Flow

```
[user]
  ↓ keystrokes
[wrapper PTY relay]                 ← keeps hot path minimal: read → write → enqueue
  ↓ async pipeline (off hot path)
[per-tool CaptureStrategy]
  ↓
  ├── PTYTeeStrategy:
  │     ↓ writes journal/sessions/<date>/<uuid>.json
  │     ↓ tools/workers/process_session.py
  │     → observations → journal/raw/, journal/inbox/, wiki/
  │
  └── ClaudeJsonlStrategy:
        ↓ reads claude-mem observations (preferred) or raw .jsonl (fallback)
        ↓ tools/workers/process_session.py
        → observations → journal/raw/, journal/inbox/, wiki/
```

The hot path on the wrapper is intentionally tiny:

1. read fd → ANSI-filter → write to other fd
2. enqueue raw bytes to `AsyncIOPipeline`

Everything else (transcript persistence, line classification, status bar updates) runs on a background thread, gated behind `EXOCORTEX_FAST_INPUT=1` while we burn it in.

## Non-Negotiable Rules

1. **Single source per tool.** A given tool has exactly one canonical capture source. Do not double-capture.
2. **No cross-tool assumptions in the wrapper.** Code that reads `~/.claude/...` lives inside `ClaudeJsonlStrategy`, not in the relay loop or the worker.
3. **claude-mem stays external.** ExoCortex does not reach into claude-mem's DB directly except through the documented observation export. If that contract is unclear, treat raw `.jsonl` as the fallback.
4. **The hot path is sacred.** Nothing that touches per-keystroke latency may be added to the relay loop without a measurement showing it's free. New work goes on the async pipeline.
5. **Fall back, don't fail.** If a tool-specific capture source is missing or malformed, the strategy degrades to PTY-tee rather than dropping the session.
6. **Curation lives in workers, not strategies.** A `CaptureStrategy` only produces a normalized event stream. Promotion into `journal/`, `wiki/`, intent queues, etc. happens in `tools/workers/`.

## What This Contract Enables

- **Phase 3** (per-tool capture strategy abstraction): the refactor that introduces `CaptureStrategy` and extracts current behavior into `PTYTeeStrategy`.
- **Phase 4** (claude-mem observation consumption): `ClaudeJsonlStrategy` reading claude-mem output instead of re-doing LLM compression on the same text.
- **Phase 5** (lazy bootstrap): orthogonal to memory but listed here because it operates on the same wrapper. The bootstrap manifest moves from synchronous file reads to a small pointer that the agent expands on demand.

## Where Each Piece of Data Lives

| Data | Owner | Path |
|---|---|---|
| Raw Claude session transcripts | Claude Code | `~/.claude/projects/<slug>/<session>.jsonl` |
| Compressed observations from Claude sessions | claude-mem | claude-mem internal DB |
| Raw Codex session transcripts | Codex | `~/.codex/sessions/<...>` |
| ExoCortex per-session log (PTY-tee mode) | ExoCortex | `journal/sessions/<date>/<uuid>.json` |
| Promoted journal entries | ExoCortex | `journal/raw/`, `journal/inbox/` |
| Curated knowledge | ExoCortex | `wiki/` |
| Agent state, health, intents | ExoCortex | `system/`, `journal/inbox/` |
| Auto-memory | Claude Code | `~/.claude/projects/<slug>/memory/` |

If a future system adds a new home for any of these, update this table.

## Open Questions

- Whether `ClaudeJsonlStrategy` should consume claude-mem's observations via a CLI/API call or by tailing claude-mem's storage directly. Tracked under Phase 4.
- Whether the `codex` strategy is worth implementing in this round or whether `PTYTeeStrategy` is good enough for codex sessions. Default: keep PTY-tee for codex until there's a concrete need.

See also: [[Operating Contract]], `tools/wrappers/README.md`, `tools/workers/README.md`.
