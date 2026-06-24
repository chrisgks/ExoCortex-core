#!/usr/bin/env python3
"""Re-summarize sessions whose stored summary is hollow (every work field
empty) but whose raw Claude ``.jsonl`` transcript still survives on disk.

Unlike ``reprocess_sessions.py`` (which only targets sessions *missing*
artifacts), this targets sessions that completed with empty output — the
failure mode caused by the claude-mem prompts-only short-circuit and the
wrong-jsonl locator bug. It clears the stale per-session artifacts and the
hollow daily-journal block (which ``append_locked_once`` would otherwise
refuse to overwrite), then re-runs ``process_session.py`` with the fixed
transcript resolution. That re-run also rebuilds the affected weekly synthesis.

Dry-run by default; pass ``--apply`` to actually reconstruct.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workers.process_session import load_session_transcript

# A summary is "hollow" when every one of these work fields is empty — the
# model had no real transcript to extract from. ``summary`` text alone (often
# just the first user line) does not count as content.
WORK_KEYS = (
    "completed_tasks",
    "decisions",
    "open_questions",
    "follow_ups",
    "what_mattered",
    "repeated_patterns",
    "model_updates",
    "signals",
)

# Minimum transcript turns to consider a session worth (and possible to)
# reconstruct. Below this the raw jsonl is itself a stub — nothing to recover.
MIN_RECOVERABLE_TURNS = 40


def iter_manifests(root: Path):
    for manifest in sorted((root / "journal" / "sessions").glob("*/*.json")):
        if manifest.name.endswith((".candidates.json", ".intelligence.json")):
            continue
        yield manifest


def artifact_path(manifest: Path, suffix: str) -> Path:
    # manifest is "<id>.json"; artifacts are "<id>.summary.intelligence.json" etc.
    return manifest.with_name(manifest.stem + suffix)


def is_hollow(manifest: Path) -> bool:
    intel = artifact_path(manifest, ".summary.intelligence.json")
    if not intel.exists():
        return False
    try:
        data = json.loads(intel.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return all(not (data.get(key) or []) for key in WORK_KEYS)


def recoverable_turns(root: Path, manifest: Path) -> int:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        _, entries = load_session_transcript(root, data)
        return len(entries)
    except Exception:
        return 0


def find_targets(root: Path) -> list[tuple[Path, int]]:
    targets: list[tuple[Path, int]] = []
    for manifest in iter_manifests(root):
        if not is_hollow(manifest):
            continue
        turns = recoverable_turns(root, manifest)
        if turns >= MIN_RECOVERABLE_TURNS:
            targets.append((manifest, turns))
    return targets


def strip_daily_block(daily_path: Path, session_id: str) -> bool:
    """Remove the ``## ...`` block carrying this session_id from a daily
    journal so the reprocess can append a fresh one. Returns True if removed.
    """
    if not daily_path.exists():
        return False
    text = daily_path.read_text(encoding="utf-8")
    marker = f"- session_id: `{session_id}`"
    # Split keeping the leading newline-anchored "## " headers as block starts.
    blocks = re.split(r"(?m)(?=^## )", text)
    kept = [b for b in blocks if marker not in b]
    if len(kept) == len(blocks):
        return False
    daily_path.write_text("".join(kept), encoding="utf-8")
    return True


def clear_stale_artifacts(root: Path, manifest: Path) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    session_id = data.get("session_id", "")
    date = (data.get("started_at") or "")[:10]
    if date:
        strip_daily_block(root / "journal" / "summarised" / f"{date}.md", session_id)
    for suffix in (
        ".summary.md",
        ".summary.intelligence.json",
        ".candidates.json",
        ".candidates.md",
    ):
        path = artifact_path(manifest, suffix)
        if path.exists():
            path.unlink()


def reconstruct(root: Path, manifest: Path, timeout_seconds: int) -> int:
    clear_stale_artifacts(root, manifest)
    command = [
        sys.executable,
        str(root / "tools" / "workers" / "process_session.py"),
        str(manifest),
    ]
    try:
        return subprocess.run(command, cwd=str(root), timeout=timeout_seconds).returncode
    except subprocess.TimeoutExpired:
        return 124


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--limit", type=int, default=None, help="Cap targets processed.")
    parser.add_argument("--only", default=None, help="Reconstruct a single session_id.")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--apply", action="store_true", help="Actually reconstruct.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    targets = find_targets(root)
    if args.only:
        targets = [t for t in targets if t[0].stem == args.only]
    if args.limit is not None:
        targets = sorted(targets, key=lambda t: -t[1])[: args.limit]

    if not args.apply:
        for manifest, turns in sorted(targets, key=lambda t: -t[1]):
            print(f"{turns:5d} turns  {manifest.relative_to(root)}")
        print(f"hollow + recoverable: {len(targets)}")
        return 0

    failures = 0
    for manifest, turns in sorted(targets, key=lambda t: -t[1]):
        print(f"reconstructing ({turns} turns): {manifest.relative_to(root)}", flush=True)
        code = reconstruct(root, manifest, args.timeout_seconds)
        if code != 0:
            failures += 1
            print(f"  failed code={code}")
    print(f"reconstructed: {len(targets) - failures}")
    print(f"failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
