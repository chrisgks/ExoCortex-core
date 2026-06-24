# Builder Skills

Builder uses shared skills from `skills/` rather than defining separate local
skill logic here.

## Default Builder Skills

- `skills/repo-intake/` - map the repo, stack, commands, and risk surface
- `skills/verification-discovery/` - find the cheapest trustworthy checks
- `skills/debug-failure/` - turn vague breakage into a concrete failing path
- `skills/fix-bug/` - patch minimally and prove the regression is closed
- `skills/implement-feature/` - extend existing patterns incrementally
- `skills/add-tests/` - add or tighten tests when verification is weak

## Use Order

1. Start with `repo-intake` if the environment is unfamiliar.
2. Run `verification-discovery` before editing.
3. Choose `debug-failure` plus `fix-bug` for breakage.
4. Choose `implement-feature` for additive work.
5. Use `add-tests` whenever the repo lacks enough coverage to trust the change.
