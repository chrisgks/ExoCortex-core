#!/usr/bin/env python3
"""One-command publish of the exocortex-core PUBLIC repo. Reusable — do not
re-derive this flow by hand.

Topology (why this exists):
  - the export builds a clean public copy into ``_exports/exocortex-core``
    (staging, gitignored in the private repo);
  - the actual public repo is a separate clone at ``~/exocortex-core`` with the
    ``exocortex-core`` GitHub remote.
The two are linked only by a copy step that kept getting hand-assembled. This
script is that copy step, made safe and repeatable.

Flow:
  1. Build the export and run the hard content gate
     (``tools/export_exocortex_core.py``). Abort on any gate failure.
  2. Mirror the gated build into ``~/exocortex-core`` with ``rsync --delete``,
     preserving its ``.git``.
  3. Re-run the content gate on the public tree itself (final defense).
  4. Stage the changes and print a diff summary.
  5. STOP and report. This NEVER commits or pushes — publishing the public repo
     needs an explicit human go with the gate green.

Usage:
    python3 tools/publish_exocortex_core.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "_exports" / "exocortex-core"
PUBLIC = Path.home() / "exocortex-core"
EXPECTED_REMOTE_SUBSTR = "exocortex-core"  # guard against overwriting the wrong repo


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], **kw)


def main() -> int:
    print("→ building export + running content gate ...")
    built = _run([sys.executable, ROOT / "tools" / "export_exocortex_core.py"], cwd=str(ROOT))
    if built.returncode != 0:
        print("✗ export/gate failed — nothing published")
        return 1

    if not (PUBLIC / ".git").exists():
        print(f"✗ {PUBLIC} is not a git repo — clone the public repo there first")
        return 1
    remote = _run(
        ["git", "-C", PUBLIC, "remote", "get-url", "origin"],
        capture_output=True, text=True,
    ).stdout.strip()
    if EXPECTED_REMOTE_SUBSTR not in remote:
        print(f"✗ {PUBLIC} origin is '{remote}', not the exocortex-core repo — refusing to overwrite")
        return 1

    print(f"→ mirroring gated build into {PUBLIC} ...")
    _run(["rsync", "-a", "--delete", "--exclude=.git", f"{STAGING}/", f"{PUBLIC}/"], check=True)

    # Final defense: gate the public tree itself, not just the staging build.
    sys.path.insert(0, str(ROOT / "tools"))
    from check_public_export import audit  # type: ignore

    flags = audit(PUBLIC)
    if flags:
        print(f"✗ ABORT: gate found {len(flags)} personal file(s) in {PUBLIC}:")
        for rel, reasons in flags:
            print(f"   {rel}: {'; '.join(reasons)}")
        return 1
    print("✓ public-content gate clean on the public tree")

    _run(["git", "-C", PUBLIC, "add", "-A"])
    stat = _run(
        ["git", "-C", PUBLIC, "diff", "--cached", "--stat"],
        capture_output=True, text=True,
    ).stdout
    print("\n=== staged changes in the public repo ===")
    print(stat.strip() or "(no changes — public repo already up to date)")

    print("\nREADY. Review, then publish manually:")
    print(f"  git -C {PUBLIC} commit -m \"<message>\"")
    print(f"  git -C {PUBLIC} push")
    print("(this script never commits or pushes — publishing needs your explicit go)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
