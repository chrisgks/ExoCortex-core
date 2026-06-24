#!/usr/bin/env bash
#
# Install the ExoCortex git-guardrails pre-commit hook.
# Symlinks skills/git-guardrails/pre-commit into .git/hooks/pre-commit so the
# tracked script stays the single source of truth.
#
# Adapted for ExoCortex from `git-guardrails-claude-code` by Matt Pocock —
# https://github.com/mattpocock/skills (MIT License).

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hooks_dir="$repo_root/.git/hooks"
src="$repo_root/skills/git-guardrails/pre-commit"
dest="$hooks_dir/pre-commit"

if [[ ! -f "$src" ]]; then
  echo "Cannot find $src" >&2
  exit 1
fi

chmod +x "$src"
mkdir -p "$hooks_dir"

if [[ -e "$dest" && ! -L "$dest" ]]; then
  echo "Existing non-symlink hook at $dest — backing up to $dest.bak" >&2
  mv "$dest" "$dest.bak"
fi

ln -sf "../../skills/git-guardrails/pre-commit" "$dest"
echo "Installed git-guardrails pre-commit hook -> $dest"
echo "Test it:  git add <a personal path> && git commit -m test   (it should block)"
