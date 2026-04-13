# Repo Split Model

ExoCortex should live as two repositories:

- `exocortex-core` (public)
- `exocortex-home` (private)

## Public Repo: `exocortex-core`

This repo should contain:

- reusable runtime code under `tools/`
- wrapper and bootstrap entrypoints
- reusable root and system contracts
- reusable agents, skills, and templates
- starter domain structure
- clean-slate `journal/`, `raw/`, and `wiki/` scaffolds
- public-safe docs, screenshots, and tests

This repo should not contain live personal state.

## Private Repo: `exocortex-home`

This repo should contain:

- your actual journal history
- your actual raw corpus and attachments
- your managed wiki contents
- personal projects and notes
- local operating state and preferences that are specific to your instance

The private repo can also contain the export tooling and whatever extra private automation you need.

## Source Of Truth

Treat the private repo as the place where you actually use ExoCortex.

Treat the public repo as a curated export of the reusable product layer.

That means:

- product logic may begin in the private repo
- public publication happens by regenerating `_exports/exocortex-core/`
- only the curated export gets pushed to `exocortex-core`

## Recommended Push Flow

1. Make or refine changes in the private repo.
2. Regenerate `_exports/exocortex-core/`.
3. Run tests and leak checks.
4. Copy the export into the public repo worktree.
5. Commit and push the public repo.
6. Commit and push the private repo separately.

## Boundary Rule

If the value is in the structure, code, or contract, it probably belongs in the public repo.

If the value is in the content of your life, work, sources, or history, it belongs in the private repo.
