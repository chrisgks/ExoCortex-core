---
name: repo-intake
description: Use when builder enters an unfamiliar codebase and needs a fast, trustworthy map of the repo, stack, commands, contract files, and likely risk surface before making changes.
---

# Repo Intake

Map the local engineering environment before editing.

## Use This Skill When

- the repo is unfamiliar
- the task starts in a new folder or project
- the stack, test runner, or build path is unclear
- local contract files may materially change how the task should be done

## Goals

- identify the stack and major subsystems
- read the local contract before making assumptions
- find likely verification commands
- identify the narrowest part of the repo that matters for the task

## Required Inputs

At the start of intake:

1. Identify the target root or working folder.
2. Read local contract files such as `README.md`, `AGENT.md`, `STATE.md`,
   `WORKFLOWS.md`, and `SKILLS.md` when present.
3. Inspect repo markers such as `package.json`, `pyproject.toml`, `Cargo.toml`,
   `go.mod`, `Makefile`, lockfiles, and CI config.

## Workflow

1. Identify the probable stack, package manager, test runner, and build surface.
2. Map the relevant directories, entrypoints, and changed-path risk areas.
3. Note the likely verification commands, but do not trust them yet.
4. Hand off to `skills/verification-discovery/` before editing.

## Evidence Rules

- ground stack claims in files, not guesses
- distinguish confirmed commands from likely commands
- prefer the narrowest relevant repo slice over a broad inventory dump

## Stop Conditions

- the stack and relevant repo slice are clear enough to proceed
- the environment is too incomplete to identify even a plausible verification path

## Do Not

- do not start editing during intake
- do not assume the repo matches your default toolchain expectations
- do not widen to unrelated subsystems just because they are present
