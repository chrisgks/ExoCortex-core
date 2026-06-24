# Open Source Notes

This export is meant to be the starting point for a public `exocortex-core` repository.

## Included

- Runtime code under `tools/`
- Reusable contracts and role definitions under `agents/`, `skills/`, `system/`, and selected root files
- Starter domain structure
- Empty scaffolds for `journal/`, `raw/`, and `wiki/`
- A real `exocortex-init` bootstrap and scaffold flow
- Docs and tests with obvious personal-machine references removed

## Excluded

- Live `journal/` contents
- Live `raw/` contents
- Live managed `wiki/` contents
- Personal project folders under `domains/*/projects/`
- Local Obsidian state
- Generated frontend artifacts

## Publication Status

This export is intended to be publishable as the public core repo.

The remaining maintainer tasks are operational rather than structural:

1. Review screenshots and example artifacts to ensure they stay synthetic and public-safe.
2. Decide whether `domains/` should remain opinionated defaults or move fully into first-run generation.
3. Keep the private/public boundary disciplined as the product evolves.
