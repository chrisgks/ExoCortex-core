# Mission Control Frontend

This is the React frontend for ExoCortex Mission Control.

## Purpose

- show active contexts and projects
- show the current action-space graph as contexts, policies, moves, and destinations
- surface inferred-intent review items from `journal/inbox/pending-intents.md`
- show lightweight telemetry from recent journal summaries
- provide a simple prompt console for launching a wrapped agent run through the backend

## Current Contract

- the radar view renders a deterministic action-space graph from the backend endpoint `/api/action-space`
- clicking a context node recenters the graph on that context
- the inbox view is intentionally scoped to inferred-intent review
- confirming an inbox card promotes it into `system/OPEN LOOPS.md`
- rejecting an inbox card records the decision in `journal/inbox/reviewed-intents.md`
- the frontend depends on the FastAPI backend in `tools/mission-control/backend/main.py`

## Notes

- this UI is exploratory and should follow the repo's markdown-first runtime model rather than becoming a second source of truth
- durable state should continue to live in markdown files under the main ExoCortex hierarchy
