---
name: git-guardrails
description: Block committing personal/instance-private paths into a public-facing repo. Use when setting up the public/private split, hardening a repo before open-sourcing, or on a 'guardrails' / 'protect against leaks' trigger. Ships a pre-commit hook that refuses staged personal paths (domains/, journal/, raw/, wiki/, exo/, the design doc, secrets) and allows normal product files.
---

# Git Guardrails

The safety net for a repo with a public/private split. A
pre-commit hook refuses any commit that stages a personal or instance-private path,
so a stranger-facing repo never receives personal data — even by accident, even from
an agent.

The original Matt Pocock skill blocks dangerous git *commands* (force push, hard
reset). This adaptation blocks personal *paths*, which is the real risk for a repo
that mixes a public core with a private instance.

## Use This Skill When

- preparing the repo for open-source / a public mirror
- a single working tree holds both shippable core and personal data
- the user says "guardrails", "protect against leaks", or "block personal paths"

## What It Blocks

A commit is refused if any staged path matches a personal pattern:

- `domains/` — personal and work domain content
- `journal/` — session and usage journals
- `raw/` — raw inbox and unprocessed captures
- `exo/` — instance-private working files
- `wiki/` — personal knowledge wiki content
- `STATE.md`, `MEMORY.md`, and other instance or internal docs
- `.env*`, `*.pem`, `secrets/`, `credentials*` — secrets

It allows everything else — `tools/`, `skills/`, `templates/`, `README.md`, tests,
and any other product file.

## The Hook

- Script: `skills/git-guardrails/pre-commit` (tracked — single source of truth).
- It reads `git diff --cached` and exits non-zero with the offending paths listed if
  any match.

## Install

```bash
bash skills/git-guardrails/install.sh
```

This symlinks the tracked script into `.git/hooks/pre-commit`. Re-runs are safe; an
existing real hook is backed up to `pre-commit.bak`.

Manual equivalent:

```bash
chmod +x skills/git-guardrails/pre-commit
ln -sf ../../skills/git-guardrails/pre-commit .git/hooks/pre-commit
```

## Operating It

- **A blocked commit** prints the offending paths and how to unstage them. Fix with
  `git restore --staged <path>`.
- **Add patterns** without editing the script: set `EXO_GUARDRAILS_EXTRA="regex1|regex2"`
  in the environment before committing. For permanent patterns, edit `BLOCKED_PATTERNS`
  in `pre-commit`.
- **Deliberate override** (you accept the risk): `git commit --no-verify`. Use only
  when you are certain a flagged path is genuinely public.

## Why A Hook (Not Just `.gitignore`)

`.gitignore` only stops *untracked* files and is silent. The hook also catches a path
that was force-added (`git add -f`) or that slipped into tracking earlier, and it
*tells you* why the commit failed. Run both: ignore as the first line, the hook as
the backstop.

## Do Not

- do not weaken the patterns to make a commit pass — unstage the personal path instead
- do not commit personal data with `--no-verify` to "fix it later"
- do not rely on the hook alone; keep `.gitignore` and the export script aligned

---

*Adapted for ExoCortex from `git-guardrails-claude-code` by Matt Pocock —
https://github.com/mattpocock/skills (MIT License, Copyright (c) 2026 Matt Pocock).
Re-purposed from blocking dangerous git commands to blocking personal-path leaks.*
