# Contributing

ExoCortex is a local-first system with a strict separation between reusable product logic and instance-local state.

## Ground Rules

- Keep product logic reusable and inspectable.
- Do not commit personal journal history, private raw sources, local Obsidian state, or generated frontend artifacts.
- Preserve the separation between:
  - runtime code under `tools/`
  - reusable contracts under root, `system/`, `agents/`, `skills/`, and templates
  - instance-local runtime state under `journal/`, `raw/`, `wiki/`, and project folders

## Development Flow

1. Run `python3 tools/bootstrap/init.py` in your clone if you need to restore the clean runtime scaffold.
2. Install wrappers with `./tools/wrappers/install.sh` if you want wrapped `codex`, `claude`, and `gemini` commands on `PATH`.
3. Run `python3 -m unittest discover -s tests` before proposing changes.

## Pull Requests

- Keep changes scoped.
- Document any user-visible runtime behavior changes in `README.md` or the relevant local docs.
- Add or update tests when you change wrapper, worker, or bootstrap behavior.
- Treat path leakage, private-state leakage, and unsafe automation as release blockers.
