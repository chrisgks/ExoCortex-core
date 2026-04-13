# ExoCortex Docs

This page is the visual tour of ExoCortex.

Use the root [../README.md](../README.md) for the product overview and quickstart. Use this page when you want the screenshots, loop visuals, and architecture posters in one place.

## Start Here

- Start with [../README.md](../README.md) if you have not read the main landing page yet.
- Read [compositional-examples.md](compositional-examples.md) if you want the composition model to become concrete.
- Read [first-5-minutes.md](first-5-minutes.md) if you want the shortest path to one working loop.
- Read [technical-architecture.md](technical-architecture.md) if you want the system model.

## Demo Loop

<p align="center">
  <img src="assets/exocortex-loop.gif" alt="ExoCortex demo loop GIF" width="100%" />
</p>

The loop above is the core promise:

1. Start in the right folder.
2. Launch a wrapped harness.
3. Capture the session and write the journal.
4. Promote durable signal.
5. Make the next session better.

## Real Terminal Walkthrough

<p align="center">
  <img src="assets/terminal-walkthrough.png" alt="ExoCortex terminal walkthrough screenshot" width="100%" />
</p>

This is based on real command output from this repo:

- `./tools/wrappers/install.sh`
- `exocortex-doctor`
- `codex --help` through the ExoCortex wrapper

## Key Screens

### Launch / Open Graph

<p align="center">
  <img src="assets/exocortex-og.png" alt="ExoCortex launch image" width="100%" />
</p>

### Mission Control

<p align="center">
  <img src="assets/mission-control.png" alt="ExoCortex Mission Control screenshot" width="100%" />
</p>

The radar view shows the live action surface: current context, routing policy, available moves, and likely destinations.

### Agent Forge

<p align="center">
  <img src="assets/mission-control-forge.png" alt="ExoCortex Agent Forge screenshot" width="100%" />
</p>

The roster stays explicit. Stable agents are part of the architecture, not hidden prompt folklore.

## Visual Architecture

### Context Boundaries

<p align="center">
  <img src="assets/architecture-context.png" alt="ExoCortex context boundary architecture poster" width="100%" />
</p>

This is the core retrieval rule: folder location determines boundary, boundary determines visible contracts, and visible contracts determine what truth comes into scope first.

### Compounding Loop

<p align="center">
  <img src="assets/architecture-loop.png" alt="ExoCortex compounding loop architecture poster" width="100%" />
</p>

This is the core systems claim: the work compounds because durable output lands back in inspectable files.

## Architecture At A Glance

### Session Compounding Loop

```mermaid
flowchart LR
    A[Start in a folder] --> B[Read local and ancestor contracts]
    B --> C[Resolve default agent and mode]
    C --> D[Launch wrapped codex or claude or gemini]
    D --> E[Capture transcript and manifest]
    E --> F[Write daily journal and session summary]
    F --> G[Extract candidates for memory workflows rules skills]
    G --> H[Inject weighted reusable context later]
    H --> B
```

### Context Hierarchy

```mermaid
flowchart TD
    A[Repo Root] --> B[System Control Plane]
    A --> C[Agents]
    A --> D[Domains]
    D --> E[Projects]
    A --> F[Journal]
    A --> G[Managed Wikis]
    E --> G
```

### Managed Knowledge Model

```mermaid
flowchart LR
    A[raw] --> B[source notes]
    B --> C[overviews entities concepts]
    C --> D[analyses]
    D --> E[future answers]
```

## What To Read Next

- [../README.md](../README.md) for the GitHub landing page and quickstart
- [compositional-examples.md](compositional-examples.md) for real-world examples such as teacher, accountant, research engineer, and editor compositions
- [first-5-minutes.md](first-5-minutes.md) for the shortest practical onboarding path
- [technical-architecture.md](technical-architecture.md) for the technical model: entities, relationships, agents, skills, tools, and composition
- [../agents/README.md](../agents/README.md) for the agent registry and role rationale
- [../tools/wrappers/README.md](../tools/wrappers/README.md) for wrapper behavior
- [../journal/README.md](../journal/README.md) for the compounding journal loop
- [../wiki/04_analyses/ExoCortex system architecture.md](../wiki/04_analyses/ExoCortex system architecture.md) for the deeper architecture writeup
- [../wiki/04_analyses/Obsidian vaults and managed wikis.md](../wiki/04_analyses/Obsidian vaults and managed wikis.md) for wiki topology

## What ExoCortex Is Not

- not a generic multi-agent playground
- not a hosted note app with opaque memory
- not a black-box memory system
- not optimized for people who never want to read the underlying files

It is optimized for people who want context, state, and cognition to stay inspectable.
