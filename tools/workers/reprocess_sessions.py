#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root

from tools.workers import logbook

QUARANTINE_STATUS = "quarantined"
QUARANTINE_LOG_REL = Path("journal") / "inbox" / "quarantine-log.md"
# Statuses that mean a manifest was intentionally not synthesized (observer/
# headless noise quarantined per spec §5 item 1b, or otherwise skipped). These
# legitimately have no summary/candidate artifacts, so they must NOT count as
# "missing artifacts" in health metrics — they are reported separately.
EXCLUDED_FROM_MISSING = (QUARANTINE_STATUS, "skipped")


def _manifest_status(manifest: Path) -> str | None:
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("summary_status")
    except (OSError, json.JSONDecodeError):
        return None


def missing_session_manifests(root: Path, limit: int | None = None) -> list[Path]:
    """Manifests that lack synthesis artifacts and were NOT intentionally
    excluded (quarantined/skipped). Excluded manifests are reported via
    ``excluded_from_missing_count`` instead so the metric reflects reality.
    """
    missing: list[Path] = []
    for manifest in sorted((root / "journal" / "sessions").glob("*/*.json")):
        if manifest.name.endswith(".intelligence.json") or manifest.name.endswith(".candidates.json"):
            continue
        if _manifest_status(manifest) in EXCLUDED_FROM_MISSING:
            continue
        stem = manifest.with_suffix("")
        if not stem.with_suffix(".summary.md").exists() or not stem.with_suffix(".candidates.json").exists():
            missing.append(manifest)
            if limit is not None and len(missing) >= limit:
                break
    return missing


def excluded_from_missing_count(root: Path, limit: int | None = None) -> int:
    """Count manifests excluded from the missing-artifacts metric because they
    were quarantined or skipped (spec §5 item 1b). Honors the same ``limit``
    window as ``missing_session_manifests`` so health can report "N of latest
    100" consistently."""
    count = 0
    seen = 0
    for manifest in sorted((root / "journal" / "sessions").glob("*/*.json")):
        if manifest.name.endswith(".intelligence.json") or manifest.name.endswith(".candidates.json"):
            continue
        if _manifest_status(manifest) in EXCLUDED_FROM_MISSING:
            count += 1
        seen += 1
        if limit is not None and seen >= limit:
            break
    return count


def _iter_session_manifests(root: Path):
    for manifest in sorted((root / "journal" / "sessions").glob("*/*.json")):
        if manifest.name.endswith(".intelligence.json") or manifest.name.endswith(".candidates.json"):
            continue
        yield manifest


def stuck_processing_manifests(root: Path, limit: int | None = None) -> list[Path]:
    """Manifests stuck at ``summary_status: processing``.

    These never finished the worker (the dominant case is claude-mem observer
    noise, see spec §5 item 1b) and so count as missing artifacts forever,
    stalling completeness metrics. Sorted oldest-first so a bounded batch clears
    the longest-stuck entries first.
    """
    stuck: list[Path] = []
    for manifest in _iter_session_manifests(root):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("summary_status") == "processing":
            stuck.append(manifest)
            if limit is not None and len(stuck) >= limit:
                break
    return stuck


def quarantine_stuck(root: Path, manifests: list[Path], *, apply: bool) -> int:
    """Mark stuck manifests as ``quarantined`` so they stop counting as missing
    artifacts. Reversible (the prior status is preserved as
    ``prior_summary_status``) and never deletes the source manifest.

    Dry-run by default: with ``apply=False`` nothing is written and 0 is
    returned. With ``apply=True`` each manifest's status is flipped and one line
    per change is appended to ``journal/inbox/quarantine-log.md``. Returns the
    number of manifests changed.
    """
    if not apply:
        return 0
    changed = 0
    log_lines: list[str] = []
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for manifest in manifests:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        prior = data.get("summary_status")
        if prior == QUARANTINE_STATUS:
            continue
        data["prior_summary_status"] = prior
        data["summary_status"] = QUARANTINE_STATUS
        data["quarantined_at"] = stamp
        try:
            manifest.write_text(
                json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError:
            continue
        changed += 1
        rel = manifest.relative_to(root)
        cwd = data.get("cwd", "")
        log_lines.append(f"- `{rel}` status `{prior}` -> `{QUARANTINE_STATUS}` cwd `{cwd}`")
        # Durable status-flip on a tracked file (spec §3/§7): reversible via the
        # preserved prior status. Recorded to the Logbook alongside the md log.
        logbook.record_change(
            root,
            actor="reprocess.quarantine-stuck",
            action="status-flip",
            path=manifest,
            summary=f"quarantined stuck manifest (status {prior} -> {QUARANTINE_STATUS})",
            reversal={
                "prior_summary_status": prior,
                "field": "summary_status",
            },
            metadata={"cwd": cwd},
        )
    if changed:
        log_path = root / QUARANTINE_LOG_REL
        log_path.parent.mkdir(parents=True, exist_ok=True)
        block = "\n".join(
            [f"## {stamp} quarantine ({changed} manifests)", "", *log_lines, ""]
        )
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(block + "\n")
    return changed


def process_manifest(root: Path, manifest: Path, timeout_seconds: int = 120) -> int:
    command = [sys.executable, str(root / "tools" / "workers" / "process_session.py"), str(manifest)]
    try:
        result = subprocess.run(command, cwd=str(root), timeout=timeout_seconds)
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reprocess sessions missing summary or candidate artifacts.")
    parser.add_argument("--root", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--apply", action="store_true", help="Actually run process_session.py (or apply quarantine).")
    parser.add_argument(
        "--quarantine-stuck",
        action="store_true",
        help=(
            "Instead of reprocessing, mark manifests stuck at "
            "summary_status=processing as quarantined so they stop counting as "
            "missing artifacts. Reversible (prior status preserved) and logged. "
            "Dry-run unless --apply is also passed."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)

    if args.quarantine_stuck:
        targets = stuck_processing_manifests(root, args.limit)
        if not args.apply:
            for manifest in targets:
                print(manifest.relative_to(root))
            print(f"would quarantine: {len(targets)} (dry-run; pass --apply to write)")
            return 0
        changed = quarantine_stuck(root, targets, apply=True)
        print(f"quarantined: {changed}")
        print(f"log: {(QUARANTINE_LOG_REL)}")
        return 0

    manifests = missing_session_manifests(root, args.limit)
    if not args.apply:
        for manifest in manifests:
            print(manifest.relative_to(root))
        print(f"missing: {len(manifests)}")
        return 1 if manifests else 0
    failures = 0
    for manifest in manifests:
        code = process_manifest(root, manifest, args.timeout_seconds)
        if code != 0:
            failures += 1
            print(f"failed: {manifest.relative_to(root)} code={code}")
    print(f"processed: {len(manifests) - failures}")
    print(f"failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
