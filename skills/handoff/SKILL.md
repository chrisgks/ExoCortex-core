---
name: handoff
description: Compact the current session into a handoff document so a fresh agent can continue, and feed the session-close summary surface. Use at session close, on context exhaustion, or on a 'hand off' / 'write a handoff' trigger. Redacts secrets and personal data; references existing artifacts instead of duplicating them.
---

# Handoff

Write a handoff document that summarizes the current session so a fresh agent can
pick up the work without re-reading the whole transcript.

## Use This Skill When

- a session is ending and the work is not finished
- context is nearly exhausted and the thread needs to continue in a new session
- the user says "hand off", "write a handoff", or "compact this for the next agent"
- closing a session that should feed the session-close summary surface

## What To Write

1. **Goal** — what this session was trying to do, in one or two lines.
2. **State** — what is done, what is in progress, what is blocked and why.
3. **Decisions** — choices made and the reasoning, so they aren't relitigated.
4. **Next actions** — the concrete next steps, ordered.
5. **Suggested skills** — which skills the next agent should invoke
   (e.g. `grilling`, `fix-bug`).
6. **Open questions** — anything unresolved that needs the user.

## Rules

- **Do not duplicate** what already lives in other artifacts — plans, specs, ADRs,
  PRs, commits, diffs, wiki notes. Reference them by path or URL instead.
- **Redact** anything sensitive or proprietary: API keys, passwords, tokens,
  personally identifiable information, and company-confidential material. The handoff
  may be read by another agent or surfaced.
- Keep it compact. A handoff that is as long as the transcript has failed.

## Where It Goes

- For a throwaway cross-session pass: write to the OS temp directory, not the
  workspace, so it doesn't get committed by accident.
- For a session close: route the summary into the session-close summary surface
  (the one file the next session actually reads on startup). Keep that entry to the
  goal, state, and next actions; link the fuller handoff if one was written.

## Do Not

- do not paste raw transcript or restate artifacts that already exist
- do not write secrets or personal data into the handoff
- do not save a draft handoff into a tracked path in a public-facing repo

---

*Adapted for ExoCortex from `handoff` by Matt Pocock —
https://github.com/mattpocock/skills (MIT License, Copyright (c) 2026 Matt Pocock).*
