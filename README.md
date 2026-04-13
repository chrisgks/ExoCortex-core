# ExoCortex

ExoCortex is a local-first, markdown-native operating layer for AI work.

This repository is the public core distribution of ExoCortex. It contains the reusable runtime, contracts,
roles, tools, templates, and clean-slate scaffolds needed to start a fresh local-first ExoCortex instance.

## What This Export Contains

- root runtime contracts
- reusable agents and skills
- wrapper runtime, workers, automations, and Mission Control source
- empty `journal/`, `raw/`, and `wiki/` scaffolds
- starter `domains/` structure
- tests and docs with personal-machine references removed

## What It Does Not Contain

- live session transcripts, summaries, or candidates
- private raw sources or attachments
- managed wiki contents from any private instance
- local Obsidian state
- generated frontend artifacts such as `node_modules/` or `dist/`

## Repository Shape

```text
ExoCortex/
  system/      control-plane policy and routing rules
  agents/      static roles
  domains/     starter domain structure
  journal/     empty runtime scaffold for session history
  raw/         empty raw-source scaffold
  skills/      reusable capabilities
  wiki/        empty managed-wiki scaffold
  tools/       wrappers, workers, automations, Mission Control source
  templates/   starter templates and placeholders
```

## Quickstart

1. Run `python3 tools/bootstrap/init.py --install-wrappers`.
2. Open a fresh shell and run `exocortex-doctor`.
3. Start a wrapped harness from the repo root or from a narrower folder such as `domains/work/`.
4. Confirm that new artifacts appear under `journal/`.
5. Scaffold your first real project with `exocortex-init project work my-project`.

## Repo Model

This public repo is intended to be the reusable `exocortex-core` layer. If you maintain a personal ExoCortex
instance as a separate private repo, keep your live journal, raw corpus, wiki contents, and personal projects
there rather than here.

## Publishing Notes

Read `OPEN_SOURCE_NOTES.md`, `RELEASE_CHECKLIST.md`, and `REPO_SPLIT.md` for the maintainer-facing publication
and split model.
