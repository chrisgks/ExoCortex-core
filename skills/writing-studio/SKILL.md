---
name: writing-studio
description: Turn a rough input — a note, a draft, a chat, or a captured seed — into a finished written piece, with you in the loop. The externalize arm's drafting skill, and the one the Ship tracker's shape hook invokes. Triggers include "shape this", "turn this into a post or essay", "draft this up".
---

# Writing Studio

Externalizing — turning a thought into a finished piece someone can read — is a
core part of the loop. This skill runs a staged pass from rough material to a
draft you can edit and publish. The process below is a sensible default; adapt
it to your own writing.

## When to use / not to use

- Use it when you point at a source (a note, a draft, a chat, or a Ship-tracker seed) and want a draft.
- Don't use it to consolidate a lot of scattered work into proposals — use **synthesis-studio**. For a plain recap, summarize inline.

## The process

### 1. Find the point
Before drafting, write the single thing the piece is saying in one plain
sentence. If you can't, you don't have the piece yet — keep reading the source or
talking it through until the point is clear. Note the form (post, essay, doc,
thread), the audience, and roughly how long it should be.

### 2. Outline
List the beats that carry the reader from not-knowing to the point: what they
need first, what follows, what to end on. Order matters more than wording here.
Cut any beat that doesn't move toward the point.

### 3. Draft
Write it through once, fast, from the outline and the source — don't polish while
drafting. Leave `[TODO]` or `[VERIFY]` markers where a fact, quote, or number
needs checking, instead of stopping to look it up.

### 4. Revise
Read it as the reader, not the writer. Cut what doesn't earn its place. Make the
opening do real work — many first paragraphs can simply be deleted. Replace vague
claims with concrete ones. Resolve every `[VERIFY]`: check each fact, quote, and
number, and remove or fix anything you can't stand behind.

### 5. Hand back
Show the draft plus a short note on what's strong, weak, and still unresolved.
You edit and decide whether to publish. The skill proposes; it never publishes
for you.

### 6. A fresh read
For anything going public, have a fresh reader — a new session, or another
person — read only the draft, not the source, and flag where it's unclear, slow,
or unconvincing. Someone who didn't write it catches what the writer can't see.

## The Ship tracker hook

`shape <id>` hands a captured seed to this skill to become a draft and marks the
item shaped.

## Guardrails

- Human-in-loop: propose, don't publish.
- Don't invent facts; mark anything unverified and check it before it ships.
- If the material involves other people or an employer, redact sensitive details first.
