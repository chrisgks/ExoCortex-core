# Compositional Examples

These examples are the practical case for ExoCortex.

The point is not that you create a brand-new agent for every need.
The point is that you can build many useful systems by composing the same stable parts in different ways.

## Reading The Examples

Each example is described as a composition of:

- **context**: where the work lives
- **agents**: which stable roles are involved
- **modes**: what stance the system takes
- **rules**: what should constrain or shape behavior
- **skills and workflows**: reusable methods
- **tools**: what actually executes the work
- **durable outputs**: what remains after the session

Some of these skills or workflows may exist today, and some may be local packages
you add for a specific context. The point of the examples is the composition
pattern, not the claim that every named method already ships as a built-in skill.

## Support vs Architecture

- **Current wrapper support**: `codex`, `claude`, `gemini`
- **Underlying architecture**: harness-agnostic

You can change tool or model provider. The infrastructure, local contracts, workflows, rules, journal, and wiki structure stay with you.

## 1. A Software Engineer Or Builder For Your Product

Suppose you are a CEO who vibecodes and you want the system to behave like a serious software engineer inside your real product repo.

This is not a new "software engineer" persona. It is mostly the existing `builder` agent, composed with the right context and a few supporting roles:

- **Context**: `domains/work/projects/my-product/` or the actual product repo root
- **Agents**: `builder` first, sometimes `planning` for scoping and `reviewer` for release-risk checks
- **Modes**: conversation, application, processing
- **Rules**:
  - prefer the smallest viable patch over broad speculative cleanup
  - inspect the repo before editing anything
  - do not claim success without running a real verification path
  - surface blockers and assumptions explicitly instead of bluffing
- **Skills and workflows**:
  - feature slicing
  - bug isolation
  - implementation workflow
  - test-and-fix loop
  - release review when the change matters
- **Tools**:
  - wrapped CLI sessions
  - local test runner
  - repo scripts
  - diffs
  - `journal/` for session summaries and follow-up signal
- **Durable outputs**:
  - code changes
  - tests or verification notes
  - clearer implementation decisions
  - reusable project-local workflows and rules

How it works:

- You start in the actual product folder, not in some abstract global workspace.
- `builder` reads the nearest local contracts and inspects the repo before touching code.
- It identifies the likely code area, the verification path, and the smallest change that could actually solve the problem.
- It implements the change, runs the relevant checks, and reports what changed, what passed, and what still looks risky.
- If the request is underspecified, `planning` can tighten the scope first.
- If the change is risky or user-facing, `reviewer` can do an adversarial pass before you trust it.

What this looks like in practice:

- You say: "Add a billing export button to the admin dashboard and make it downloadable as CSV."
- `builder` inspects the app structure, finds the dashboard code, finds the export path or missing backend route, and checks how the repo currently verifies frontend and API changes.
- It makes the smallest working slice instead of trying to redesign billing, exports, permissions, and admin UX all at once.
- It runs the tests or build that actually matter.
- It leaves behind a diff, verification evidence, and a session summary you can come back to later.

Why this is valuable for a vibecoding CEO:

- you do not have to restate the entire product every session
- the system works inside the real repo instead of in detached chat fantasies
- the builder behaves like an execution role, not a brainstorming toy
- the repo can gradually accumulate better local rules, workflows, and context as you ship

That composition gives you a practical software-engineering copilot without inventing a fake permanent "CTO agent."

## 2. A Teacher For Topic X

Suppose you want a "teacher" for `linear algebra`.

This is not a new teacher agent. It is a composition:

- **Context**: `domains/learning/projects/linear-algebra/`
- **Agents**: `research`, `planning`, `knowledge-steward`
- **Modes**: ingestion, synthesis, conversation
- **Rules**:
  - prefer primary sources and strong textbooks
  - separate intuitive explanations from formal definitions
  - promote stable concepts into the wiki only after repeated use
- **Skills and workflows**:
  - source ingestion
  - lesson-plan drafting
  - quiz or exercise generation
  - misconception tracking
- **Tools**:
  - wrapped CLI sessions
  - `raw/` for source captures
  - `wiki/` for concept pages and analyses
  - `journal/` for summaries and follow-up questions
- **Durable outputs**:
  - concept pages
  - lesson outlines
  - exercises and answer keys
  - a queue of weak spots to revisit

How it works:

- `research` reads and extracts the source material.
- `knowledge-steward` turns stable concepts into structured notes.
- `planning` turns that material into a teaching sequence.
- conversation mode makes the system behave like a tutor during live sessions.

That composition gives you a teacher.

## 3. An Accountant Or Bookkeeper

Suppose you want a local operating system for personal or small-business finance.

This is not a dedicated accountant agent. It is a composition:

- **Context**: `domains/life/projects/finance/` or `domains/work/projects/operations/`
- **Agents**: `life-systems`, `planning`, `reviewer`, sometimes `builder`
- **Modes**: ingestion, processing, review
- **Rules**:
  - never silently classify uncertain transactions
  - flag anomalies instead of forcing confident answers
  - preserve an audit trail for changes and reconciliations
- **Skills and workflows**:
  - receipt intake
  - monthly close checklist
  - anomaly review
  - recurring expense classification
  - tax packet preparation
- **Tools**:
  - wrapped CLI sessions
  - CSV imports
  - spreadsheet exports
  - local scripts for reconciliation
  - raw document storage
- **Durable outputs**:
  - categorized transactions
  - review queues for uncertain items
  - monthly summaries
  - checklists and rules for future bookkeeping

How it works:

- `life-systems` handles the operational surface.
- `planning` structures recurring close processes.
- `reviewer` checks for inconsistencies or suspicious items.
- `builder` can automate imports or reconciliation logic when needed.

That composition gives you an accountant-like system without inventing a separate accountant persona.

## 4. A Research Engineer

Suppose you want a system that can go from reading papers to changing code.

- **Context**: `domains/work/projects/my-product/`
- **Agents**: `research`, `builder`, `reviewer`
- **Modes**: ingestion, application, synthesis
- **Rules**:
  - do not overclaim from weak papers or benchmarks
  - verify changes against local tests and project constraints
  - separate evidence gathering from implementation judgment
- **Skills and workflows**:
  - paper comparison
  - experiment design
  - implementation workflow
  - code review
- **Tools**:
  - wrapped CLI sessions
  - local test runner
  - benchmarks
  - project wiki
  - journal summaries
- **Durable outputs**:
  - source notes
  - experiment decisions
  - code changes
  - review findings
  - reusable implementation heuristics

That composition gives you a research engineer.

## 5. A Writing Editor

Suppose you want a system that helps produce better essays over time.

- **Context**: `domains/writing/projects/essays/`
- **Agents**: `planning`, `research`, `reviewer`
- **Modes**: conversation, synthesis, compression
- **Rules**:
  - preserve the author's actual thesis
  - distinguish source claims from interpretation
  - critique structure and argument, not just sentence polish
- **Skills and workflows**:
  - outline generation
  - source comparison
  - revision passes
  - style review
- **Tools**:
  - wrapped sessions
  - source notes in `raw/`
  - managed notes in `wiki/`
  - draft files in the project
- **Durable outputs**:
  - outlines
  - source summaries
  - critique passes
  - improved drafting workflows

That composition gives you an editor.

## 6. A Chief Of Staff For A Project

Suppose you want a system that keeps a project moving, not just answers isolated questions.

- **Context**: project root
- **Agents**: `chief-of-staff`, `planning`, `reviewer`
- **Modes**: conversation, processing, synthesis
- **Rules**:
  - optimize for next useful action, not abstract completeness
  - surface blocked work early
  - prefer explicit handoffs over hidden context
- **Skills and workflows**:
  - backlog review
  - next-action selection
  - handoff packet generation
  - postmortem or review workflows
- **Tools**:
  - review queues
  - state files
  - journal summaries
  - task documents
- **Durable outputs**:
  - clearer priorities
  - executable next actions
  - handoff notes
  - better operating workflows

That composition gives you a project chief of staff.

## Why These Examples Matter

The same small role set can produce very different systems because the composition changes:

- context changes what is true
- modes change stance
- rules change constraints
- skills change methods
- tools change execution
- durable outputs change what future sessions inherit

That is the real point of ExoCortex.

It is not trying to ship a thousand tiny agents.
It is trying to give you a stable set of parts that can be recombined into many useful systems without losing clarity.
