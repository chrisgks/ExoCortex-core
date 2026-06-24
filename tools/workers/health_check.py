#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root

from tools.workers import context_hygiene, reprocess_sessions, review


@dataclass
class HealthItem:
    name: str
    status: str
    detail: str


def latest_session_manifest(root: Path) -> Path | None:
    manifests = sorted((root / "journal" / "sessions").glob("*/*.json"), reverse=True)
    return manifests[0] if manifests else None


def count_raw_inbox(root: Path) -> int:
    raw = root / "raw" / "inbox"
    if not raw.exists():
        return 0
    return len([path for path in raw.rglob("*") if path.is_file() and path.name != "README.md"])


def build_health(root: Path) -> list[HealthItem]:
    findings = context_hygiene.run_checks(
        root,
        context_hygiene.DEFAULT_ACTIVE_FILE_LIMIT,
        context_hygiene.DEFAULT_PRELOAD_RISK_LIMIT,
        context_hygiene.DEFAULT_PENDING_LIMIT,
        context_hygiene.DEFAULT_STALE_DAYS,
    )
    warn_count = sum(1 for finding in findings if finding.severity == "warn")
    info_count = sum(1 for finding in findings if finding.severity == "info")
    pending_count = len(review.pending_records(root))
    missing_count = len(reprocess_sessions.missing_session_manifests(root, limit=100))
    quarantined_count = reprocess_sessions.excluded_from_missing_count(root)
    raw_count = count_raw_inbox(root)
    latest_manifest = latest_session_manifest(root)
    logbook_path = root / "journal" / "logbook.jsonl"
    logbook_entries = (
        len(logbook_path.read_text(encoding="utf-8").splitlines())
        if logbook_path.exists()
        else 0
    )

    items = [
        HealthItem(
            "hygiene",
            "warn" if warn_count else "pass",
            f"{warn_count} warn, {info_count} info",
        ),
        HealthItem(
            "session_artifacts",
            "warn" if missing_count else "pass",
            f"{missing_count} of latest 100 manifests missing artifacts"
            + (f"; {quarantined_count} quarantined/skipped excluded" if quarantined_count else ""),
        ),
        HealthItem(
            "candidate_queue",
            "warn" if pending_count > context_hygiene.DEFAULT_PENDING_LIMIT else "pass",
            f"{pending_count} pending candidates",
        ),
        HealthItem(
            "raw_inbox",
            "info" if raw_count else "pass",
            f"{raw_count} files waiting",
        ),
        HealthItem(
            "latest_session",
            "pass" if latest_manifest else "warn",
            str(latest_manifest.relative_to(root)) if latest_manifest else "no session manifests found",
        ),
        HealthItem(
            "logbook",
            "pass" if logbook_entries else "info",
            f"{logbook_entries} entries" if logbook_entries else "empty",
        ),
    ]
    return items


def render(items: list[HealthItem]) -> str:
    lines = ["# ExoCortex Health", ""]
    for item in items:
        lines.append(f"- {item.status}: {item.name} - {item.detail}")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report ExoCortex operational health.")
    parser.add_argument("--root", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)
    items = build_health(root)
    if args.json:
        print(json.dumps([asdict(item) for item in items], indent=2))
    else:
        print(render(items), end="")
    return 1 if any(item.status == "warn" for item in items) else 0


if __name__ == "__main__":
    raise SystemExit(main())
