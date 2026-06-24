# Agent Registry

This folder stores the static agent roles for ExoCortex.

The role set is intentionally small. These agents are meant to represent stable
work functions that recur across many contexts, not one-off personas for
individual projects.

Each agent has:

- `README.md`
- `AGENT.md`
- `MEMORY.md`
- `STATE.md`
- `WORKFLOWS.md`
- `SKILLS.md`
- `DECISION RULES.md`

Local contexts may add light overrides, but the core role definition lives here.

Current agents:

- `chief-of-staff`: top-level orchestration and routing
- `planning`: plans, sequencing, and handoff structure
- `research`: evidence gathering and synthesis
- `builder`: implementation and verification in technical systems
- `reviewer`: adversarial review and quality control
- `knowledge-steward`: managed wiki maintenance and source ingestion
- `life-systems`: personal operations, logistics, and routines

Why this shape:

- agents are stable execution roles
- context boundaries carry the local truth
- modes change stance without requiring a new role
- skills add reusable capabilities

See [`docs/technical-architecture.md`](../docs/technical-architecture.md) for the full explanation of how agents, skills, tools, modes, and contexts compose.
