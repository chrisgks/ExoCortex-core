#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root
ACTIVE_CONTEXT_NAMES = {
    "README.md",
    "AGENT.md",
    "MEMORY.md",
    "STATE.md",
    "WORKFLOWS.md",
    "SKILLS.md",
    "DECISION RULES.md",
    "SELF MODEL.md",
    "PERSONA CALIBRATION.md",
    "HEALTH STATE.md",
    "HEALTH RULES.md",
    "OPEN LOOPS.md",
    "PRIORITIES.md",
}
IGNORED_PARTS = {
    ".git",
    "_exports",
    "_external",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "data",
    "aws_data",
}
DEFAULT_ACTIVE_FILE_LIMIT = 8000
DEFAULT_PRELOAD_RISK_LIMIT = 4000
DEFAULT_PENDING_LIMIT = 100
DEFAULT_STALE_DAYS = 30
STATUS_PATH = Path("journal/inbox/hygiene-status.md")
REPORT_DIR = Path("journal/context-hygiene")
SURFACE_NOW = Path("journal/inbox/surface-now.md")
SURFACE_NOW_ARCHIVE = Path("journal/inbox/surface-now-archive.md")


@dataclass
class Finding:
    severity: str
    category: str
    path: str
    message: str
    action: str


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def ignored(path: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.parts)


def _walk_files(root: Path) -> Iterable[Path]:
    """Yield every file under ``root``, pruning ignored subtrees, the journal,
    and nested git repositories *during* traversal.

    The hygiene checks only look for a handful of named context/state files at
    the repo and scope roots. Two subtrees made a naive ``rglob`` walk dominate
    brief/health render time (8s+ and growing):

    - ``journal`` holds ~10k generated .md files (none of them context files;
      journal READMEs were already filtered out).
    - the research projects under ``domains/`` are each their *own* git repo
      with large data/venv trees (~425k files total). ExoCortex's scope files
      live at each repo's *root*, so we yield those (they appear in the repo
      root's filenames before we stop) and then refuse to descend into the
      repo's internals. ExoCortex deliberately does not deep-scan separate
      projects — that is the correct altitude as well as the fast one.

    ``os.walk`` with in-place dir pruning skips these subtrees entirely, taking
    render to well under a second regardless of how large the projects grow."""
    import os

    pruned = IGNORED_PARTS | {"journal"}
    root_str = os.fspath(root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        nested_repo = dirpath != root_str and (".git" in dirnames or ".git" in filenames)
        base = Path(dirpath)
        for name in filenames:
            yield base / name
        if nested_repo:
            dirnames[:] = []  # capture this repo's root files, skip its guts
        else:
            dirnames[:] = [d for d in dirnames if d not in pruned]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def active_context_files(root: Path) -> Iterable[Path]:
    for path in _walk_files(root):
        if path.suffix != ".md":
            continue
        parts = path.relative_to(root).parts
        if path.name in ACTIVE_CONTEXT_NAMES or path.name == "wiki-map.md":
            yield path
        elif path.name == "index.md" and "wiki" in parts:
            yield path


def dated_tokens(text: str) -> list[datetime]:
    dates: list[datetime] = []
    for match in re.finditer(r"\b(20\d{2}-\d{2}-\d{2})\b", text):
        try:
            dates.append(datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return dates


def section_length(text: str, heading: str) -> int:
    lines = text.splitlines()
    in_section = False
    count = 0
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            in_section = line.strip().lower() == heading.lower()
            continue
        if in_section and line.strip():
            count += 1
    return count


def check_context_files(root: Path, active_limit: int, preload_limit: int) -> list[Finding]:
    findings: list[Finding] = []
    for path in active_context_files(root):
        size = len(read_text(path))
        rel_path = rel(root, path)
        if size > active_limit:
            findings.append(
                Finding(
                    "warn",
                    "active_context_size",
                    rel_path,
                    f"active context file is {size} chars, above {active_limit}",
                    "compact active content into current focus plus links to history",
                )
            )
        elif size > preload_limit:
            findings.append(
                Finding(
                    "info",
                    "preload_truncation_risk",
                    rel_path,
                    f"file is {size} chars, above the per-file preload target {preload_limit}",
                    "prefer compact summaries and move historical detail to wiki or journal",
                )
            )
    return findings


def check_state_files(root: Path, stale_days: int) -> list[Finding]:
    findings: list[Finding] = []
    cutoff = now().timestamp() - (stale_days * 86400)
    for path in _walk_files(root):
        if path.name != "STATE.md":
            continue
        text = read_text(path)
        rel_path = rel(root, path)
        if section_length(text, "## Current Focus") == 0:
            findings.append(
                Finding(
                    "warn",
                    "state_missing_focus",
                    rel_path,
                    "STATE.md has no populated Current Focus section",
                    "add one live focus item or mark the context inactive",
                )
            )
        dates = dated_tokens(text)
        if dates and max(dt.timestamp() for dt in dates) < cutoff:
            findings.append(
                Finding(
                    "info",
                    "state_stale_dates",
                    rel_path,
                    f"newest dated assertion is older than {stale_days} days",
                    "review whether the context is still active or should be archived",
                )
            )
    return findings


def count_candidate_blocks(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in read_text(path).splitlines() if line.startswith("### "))


def check_inbox(root: Path, pending_limit: int, stale_days: int) -> list[Finding]:
    findings: list[Finding] = []
    inbox = root / "journal" / "inbox"
    for path in sorted(inbox.glob("pending-*.md")):
        count = count_candidate_blocks(path)
        if count > pending_limit:
            findings.append(
                Finding(
                    "warn",
                    "pending_queue_size",
                    rel(root, path),
                    f"pending queue has {count} candidate blocks, above {pending_limit}",
                    "run exocortex-review stats/list and accept or reject the highest-signal items",
                )
            )
    surface = root / SURFACE_NOW
    if surface.exists() and read_text(surface).strip():
        age_days = (now().timestamp() - surface.stat().st_mtime) / 86400
        severity = "warn" if age_days >= stale_days else "info"
        findings.append(
            Finding(
                severity,
                "surface_now_nonempty",
                rel(root, surface),
                f"surface-now has pending content, age {age_days:.1f} days",
                "review and clear it after the surfaced item is handled",
            )
        )
    return findings


def check_raw_inbox(root: Path) -> list[Finding]:
    raw_inbox = root / "raw" / "inbox"
    if not raw_inbox.exists():
        return []
    files = [
        path
        for path in raw_inbox.rglob("*")
        if path.is_file() and path.name != "README.md" and not ignored(path)
    ]
    if not files:
        return []
    return [
        Finding(
            "info",
            "raw_inbox",
            rel(root, raw_inbox),
            f"raw inbox contains {len(files)} files",
            "triage or ingest raw files through knowledge-steward",
        )
    ]


def check_wiki_map(root: Path) -> list[Finding]:
    wiki_map = root / "wiki-map.md"
    if not wiki_map.exists():
        return [
            Finding(
                "warn",
                "wiki_map_missing",
                "wiki-map.md",
                "wiki-map.md is missing",
                "regenerate the wiki discovery map",
            )
        ]
    wiki_files = [
        path
        for path in _walk_files(root)
        if path.suffix == ".md" and path.parent.name == "wiki" and path.name != "README.md"
    ]
    if wiki_files and max(path.stat().st_mtime for path in wiki_files) > wiki_map.stat().st_mtime:
        return [
            Finding(
                "info",
                "wiki_map_stale",
                "wiki-map.md",
                "one or more wiki files are newer than wiki-map.md",
                "refresh wiki-map after wiki edits",
            )
        ]
    return []


def check_session_artifacts(root: Path, limit: int = 100) -> list[Finding]:
    sessions = sorted((root / "journal" / "sessions").glob("*/*.json"), reverse=True)[:limit]
    missing = 0
    for manifest in sessions:
        stem = manifest.with_suffix("")
        if not stem.with_suffix(".summary.md").exists() or not stem.with_suffix(".candidates.json").exists():
            missing += 1
    if not missing:
        return []
    return [
        Finding(
            "warn",
            "session_artifacts_incomplete",
            "journal/sessions",
            f"{missing} of the latest {len(sessions)} session manifests are missing summary or candidate artifacts",
            "rerun session processing or inspect failed manifests",
        )
    ]


def run_checks(
    root: Path,
    active_limit: int,
    preload_limit: int,
    pending_limit: int,
    stale_days: int,
) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_context_files(root, active_limit, preload_limit))
    findings.extend(check_state_files(root, stale_days))
    findings.extend(check_inbox(root, pending_limit, stale_days))
    findings.extend(check_raw_inbox(root))
    findings.extend(check_wiki_map(root))
    findings.extend(check_session_artifacts(root))
    findings.sort(key=lambda item: ({"warn": 0, "info": 1}.get(item.severity, 2), item.category, item.path))
    return findings


def render_report(findings: list[Finding]) -> str:
    generated = now().isoformat()
    lines = [
        "# Context Hygiene Status",
        "",
        f"- generated_at: `{generated}`",
        f"- findings: `{len(findings)}`",
        "",
    ]
    if not findings:
        lines.append("- No hygiene findings.")
        lines.append("")
        return "\n".join(lines)
    for finding in findings:
        lines.extend(
            [
                f"## {finding.severity} | {finding.category} | {finding.path}",
                "",
                f"- message: {finding.message}",
                f"- action: {finding.action}",
                "",
            ]
        )
    return "\n".join(lines)


def write_status(root: Path, report: str) -> Path:
    path = root / STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    return path


def archive_surface_now(root: Path) -> bool:
    surface = root / SURFACE_NOW
    if not surface.exists() or not read_text(surface).strip():
        return False
    archive = root / SURFACE_NOW_ARCHIVE
    archive.parent.mkdir(parents=True, exist_ok=True)
    block = "\n".join(
        [
            f"## archived {now().isoformat()}",
            "",
            read_text(surface).rstrip(),
            "",
        ]
    )
    with archive.open("a", encoding="utf-8") as handle:
        handle.write(block)
    surface.write_text("", encoding="utf-8")
    return True


def log_maintenance(root: Path, command: str, reason: str, paths: list[Path | str], metadata: dict[str, object] | None = None) -> None:
    from tools.workers import logbook

    logbook.append_entry(
        root,
        command=command,
        authority="safe_apply",
        reason=reason,
        paths=paths,
        metadata=metadata,
    )


def refresh_wiki_map(root: Path) -> bool:
    from tools.workers import wiki_map_maintain

    before = (root / "wiki-map.md").read_text(encoding="utf-8") if (root / "wiki-map.md").exists() else ""
    after = wiki_map_maintain.refresh(root, apply=True)
    return before != after


def reprocess_sessions(root: Path, limit: int, timeout_seconds: int = 120) -> tuple[int, int]:
    from tools.workers import reprocess_sessions as reprocessor

    manifests = reprocessor.missing_session_manifests(root, limit)
    failures = 0
    for manifest in manifests:
        if reprocessor.process_manifest(root, manifest, timeout_seconds) != 0:
            failures += 1
    return len(manifests) - failures, failures


def ingest_raw(root: Path, limit: int) -> int:
    from tools.workers import ingest_raw as raw_ingest

    items = raw_ingest.discover_raw_items(root, limit)
    changed_paths: list[Path] = []
    for item in items:
        changed_paths.extend([item.source_note, raw_ingest.processed_destination(root, item.path)])
        raw_ingest.ingest_item(root, item)
    if items:
        refresh_wiki_map(root)
        changed_paths.append(root / "wiki-map.md")
        log_maintenance(
            root,
            "exocortex-hygiene apply --ingest-raw",
            "raw inbox ingestion approved",
            changed_paths,
            {"count": len(items)},
        )
    return len(items)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check and maintain ExoCortex context hygiene.")
    parser.add_argument("--root", default=None, help="ExoCortex repository root.")
    parser.add_argument("--json", action="store_true", help="Print JSON findings.")
    parser.add_argument("--write-report", action="store_true", help="Write journal/inbox/hygiene-status.md.")
    parser.add_argument("--active-file-limit", type=int, default=DEFAULT_ACTIVE_FILE_LIMIT)
    parser.add_argument("--preload-risk-limit", type=int, default=DEFAULT_PRELOAD_RISK_LIMIT)
    parser.add_argument("--pending-limit", type=int, default=DEFAULT_PENDING_LIMIT)
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    subparsers = parser.add_subparsers(dest="command")
    check_parser = subparsers.add_parser("check", help="Report hygiene findings.")
    check_parser.add_argument("--json", action="store_true", help="Print JSON findings.")
    check_parser.add_argument("--write-report", action="store_true", help="Write journal/inbox/hygiene-status.md.")
    apply_parser = subparsers.add_parser("apply", help="Apply explicitly safe hygiene actions.")
    apply_parser.add_argument("--json", action="store_true", help="Print JSON findings.")
    apply_parser.add_argument("--write-report", action="store_true", help="Write journal/inbox/hygiene-status.md.")
    apply_parser.add_argument("--archive-surface-now", action="store_true")
    apply_parser.add_argument("--refresh-wiki-map", action="store_true")
    apply_parser.add_argument("--reprocess-sessions", action="store_true")
    apply_parser.add_argument("--reprocess-limit", type=int, default=10)
    apply_parser.add_argument("--reprocess-timeout-seconds", type=int, default=120)
    apply_parser.add_argument("--ingest-raw", action="store_true")
    apply_parser.add_argument("--ingest-limit", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "check"
    root = resolve_instance_root(args.root)
    findings = run_checks(
        root,
        args.active_file_limit,
        args.preload_risk_limit,
        args.pending_limit,
        args.stale_days,
    )
    actions: list[str] = []
    if command == "apply" and args.archive_surface_now:
        if archive_surface_now(root):
            actions.append("archived surface-now")
            log_maintenance(
                root,
                "exocortex-hygiene apply --archive-surface-now",
                "surface-now item handled",
                [root / SURFACE_NOW, root / SURFACE_NOW_ARCHIVE],
            )
    if command == "apply" and args.refresh_wiki_map:
        if refresh_wiki_map(root):
            actions.append("refreshed wiki-map")
            log_maintenance(
                root,
                "exocortex-hygiene apply --refresh-wiki-map",
                "wiki-map drift detected",
                [root / "wiki-map.md"],
            )
        else:
            actions.append("wiki-map already current")
    if command == "apply" and args.reprocess_sessions:
        processed, failures = reprocess_sessions(root, args.reprocess_limit, args.reprocess_timeout_seconds)
        actions.append(f"reprocessed sessions: processed={processed}, failed={failures}")
        log_maintenance(
            root,
            "exocortex-hygiene apply --reprocess-sessions",
            "session artifact backlog maintenance",
            [root / "journal" / "sessions"],
            {"processed": processed, "failed": failures, "limit": args.reprocess_limit},
        )
    if command == "apply" and args.ingest_raw:
        count = ingest_raw(root, args.ingest_limit)
        actions.append(f"ingested raw files: {count}")
    if command == "apply":
        findings = run_checks(
            root,
            args.active_file_limit,
            args.preload_risk_limit,
            args.pending_limit,
            args.stale_days,
        )
    report = render_report(findings)
    if args.write_report or command == "apply":
        write_status(root, report)
    if args.json:
        print(json.dumps({"findings": [asdict(item) for item in findings], "actions": actions}, indent=2))
    else:
        print(report)
        for action in actions:
            print(f"action: {action}")
    return 1 if any(item.severity == "warn" for item in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
