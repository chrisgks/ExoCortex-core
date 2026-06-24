---
name: grilling
description: Interview relentlessly until every open decision is resolved. Use to stress-test a plan or spec before building, to sharpen an idea, or on any 'grill me' / 'grill this' / 'poke holes' trigger. Walks the decision tree one branch at a time, recommending an answer for each open question.
---

# Grilling

Interview the user relentlessly until the plan, spec, or idea has no unresolved
branches. The output is not prose — it is a settled set of decisions.

## Use This Skill When

- a plan or spec is about to drive real work and you want it stress-tested first
- an idea needs an adversarial sharpen pass before it earns a draft
- the user says "grill me", "grill this", "poke holes", "interview me", or
  "stress-test this"

## The Loop

1. Map the decision tree. List every open question and every place two decisions
   depend on each other.
2. Walk it one branch at a time. Resolve dependencies in order — a downstream
   question waits for the decision it hangs on.
3. Ask **one question at a time**. Wait for the answer before the next. A wall of
   questions is bewildering and gets skimmed.
4. For each question, **state your recommended answer** and why. The user confirms,
   overrides, or refines — they are not starting from blank.
5. If a question can be answered by reading the codebase, the repo, or the existing
   notes, go read it instead of asking.
6. Stop when no branch is open: every decision is made, every dependency resolved.

## Output

- a short, ordered list of the decisions reached (the settled tree)
- any decisions explicitly deferred, with what would unblock them
- if feeding a spec or plan, the resolved decisions become its locked section

## Do Not

- do not ask several questions in one turn
- do not ask what the codebase or notes already answer
- do not leave a branch open and call it done
- do not write a draft, plan, or spec on top of unresolved decisions

---

*Adapted for ExoCortex from `grilling` by Matt Pocock —
https://github.com/mattpocock/skills (MIT License, Copyright (c) 2026 Matt Pocock).*
