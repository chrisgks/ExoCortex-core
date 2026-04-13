# Release Checklist

Use this checklist before updating the public `exocortex-core` repository from the private working tree.

## Export

1. Regenerate the public export:
   `python3 tools/export_public_seed.py`
2. Review the generated tree at `_exports/exocortex-core/`.
3. Confirm the export does not contain live journal history, raw corpus data, managed wiki content, local Obsidian state, or generated frontend artifacts.

## Verification

1. Run source-repo tests:
   `python3 -m unittest discover -s tests`
2. Run exported-repo tests:
   `cd _exports/exocortex-core && python3 -m unittest discover -s tests`
3. Run a leak scan against the export:
   `rg -n '/Users/|@gmail.com|@icloud.com|@me.com|sk-' _exports/exocortex-core`
4. Spot-check demo assets and docs for machine-specific or personal content.

## Publish

1. Copy or sync `_exports/exocortex-core/` into the public repo working tree.
   Suggested:
   `rsync -a --delete _exports/exocortex-core/ /path/to/exocortex-core/`
2. Commit the public repo update with a message that describes the exported change set.
3. Push the public repo to GitHub.
4. Commit the private repo changes separately, including any personal-state changes that should remain private.
5. Push the private repo to GitHub.

## Do Not Publish

- `journal/` history from the live private instance
- `raw/` sources or assets from the live private instance
- managed `wiki/` contents from the live private instance
- `.obsidian/` state
- `node_modules/`, `dist/`, `build/`, caches, or machine-local logs
