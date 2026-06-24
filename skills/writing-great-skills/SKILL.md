---
name: writing-great-skills
description: House-style reference for writing predictable, well-edited ExoCortex skills. Use when authoring or reviewing a SKILL.md, when a contributor opens a skill PR, or on a 'write a skill' / 'review this skill' trigger. Optimizes for predictable agent behavior, not identical output.
---

# Writing Great Skills

A reference for writing skills that an agent follows reliably. ExoCortex is
open-source — others will contribute skills — so this is the contract a skill must
meet to be merged.

## Use This Skill When

- authoring a new `skills/<name>/SKILL.md`
- reviewing a skill contribution before it lands
- a skill behaves unpredictably and needs tightening

## Core Principle: Predictability

A good skill makes the agent's **process** predictable, not its output. The same
inputs should produce the same steps, even when the prose differs. Optimize every
decision below for that.

## The Convention (ExoCortex)

- One folder per skill: `skills/<name>/`, containing `SKILL.md`.
- Frontmatter has exactly `name` and `description`. The `name` matches the folder.
- Optional: bundled scripts, references, and agent metadata in the same folder.
- The body is markdown: a short purpose line, then sections.

## Writing the Description

The `description` is how the skill gets found and chosen.

- **Front-load the leading word** — the verb or concept that anchors the skill.
- List the **distinct trigger branches** (the situations and the trigger phrases).
- Do not list synonyms of a branch already covered. Every word adds context cost.
- State when *not* to use it if it overlaps a sibling skill.

## Structuring the Body

Skills carry two kinds of content; keep them separate.

1. **Steps** — ordered actions with checkable completion criteria. Use these for
   anything sequential where rushing ahead breaks the result.
2. **Reference** — definitions and rules consulted as needed, not in order.

Push long supporting material to a separate file in the folder and point at it, so
the main SKILL.md stays scannable.

## Granularity

- Split a skill into two only when each half has **independent reach** (gets invoked
  on its own), or when keeping them together would let the agent declare a sequential
  job done before the later steps run.
- Otherwise keep it one skill. Do not add a third skill that overlaps two existing
  ones — extend wording instead (e.g. ExoCortex keeps `debug-failure` + `fix-bug`
  aligned rather than adding `diagnosing-bugs`).

## Failure Modes To Edit Out

- **Premature completion** — steps that let the agent stop early. Add a hard stop
  condition.
- **Duplication** — the same instruction said twice in different words.
- **Sediment** — outdated lines left over from earlier versions.
- **Sprawl** — length that buries the actual instructions.
- **No-ops** — lines that change no behavior. Cut them.

## Stop Condition

The skill is done when a fresh agent could follow it to the same process, every
section earns its place, and nothing above is left in the document.

---

*Adapted for ExoCortex from `writing-great-skills` by Matt Pocock —
https://github.com/mattpocock/skills (MIT License, Copyright (c) 2026 Matt Pocock).*
