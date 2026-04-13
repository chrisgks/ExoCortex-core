# Root State

## Current Focus

- Initialize this ExoCortex instance for real use.

## Next Recommended Actions

1. Run `python3 tools/bootstrap/init.py --install-wrappers --install-cron`.
2. Start one wrapped session from the repo root.
3. Confirm that `journal/` receives artifacts.
4. Add the first real domain project with `exocortex-init project ...`.

## Open Questions

- Which domains actually matter for this instance?
- Which long-lived workflows should become explicit contracts?
- Which live state belongs at root, domain, project, or system scope?
