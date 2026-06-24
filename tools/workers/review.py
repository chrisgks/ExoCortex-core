#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root

from tools.workers import process_session, logbook


REVIEW_STATE = Path("journal/inbox/review-state.json")
REVIEWED_ACCEPTED = Path("journal/inbox/reviewed-accepted.md")
REVIEWED_REJECTED = Path("journal/inbox/reviewed-rejected.md")
REVIEWED_EXPIRED = Path("journal/inbox/reviewed-expired.md")
# Append-only training dataset: one record per review decision (spec §7).
DECISIONS_LOG = Path("journal/inbox/review-decisions.jsonl")
TERMINAL_ACTIONS = {"accepted", "rejected", "expired"}
# Map internal terminal-state verbs onto the stable decision labels logged for training.
DECISION_LABELS = {
    "accepted": "accept",
    "rejected": "reject",
    "expired": "expire",
    "deferred": "defer",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record_key(record: dict[str, Any]) -> str:
    return "|".join(
        [
            record.get("candidate_type", ""),
            record.get("normalized_key", ""),
            record.get("suggested_destination", ""),
        ]
    )


def content_hash(record: dict[str, Any]) -> str:
    """Stable identity of a candidate by its content, independent of destination.

    Used both for cross-queue dedup and as the candidate id in the decisions log,
    so the same idea logged across sessions remains joinable for later training.
    """
    basis = normalize_content(record.get("text", ""))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def normalize_content(text: str) -> str:
    return " ".join(text.lower().split()).strip().rstrip(".")


def load_state(root: Path) -> dict[str, Any]:
    path = root / REVIEW_STATE
    if not path.exists():
        return {"items": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"items": {}}
    if not isinstance(payload.get("items"), dict):
        payload["items"] = {}
    return payload


def save_state(root: Path, state: dict[str, Any]) -> None:
    path = root / REVIEW_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_records(root: Path) -> list[dict[str, Any]]:
    return process_session.aggregate_candidate_records(process_session.load_candidate_records(root))


def confidence_rank(record: dict[str, Any]) -> int:
    return process_session.confidence_value(record.get("confidence", "low"))


def dedup_by_content(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse records that share the same content hash.

    Candidate aggregation already dedups by (type, key, destination); the same
    idea can still surface under more than one suggested destination. The review
    loop must never show the same content twice, so we keep the strongest copy
    (highest confidence, then highest score, then most evidence) and drop the rest.
    """
    best: dict[str, dict[str, Any]] = {}
    for record in records:
        key = content_hash(record)
        incumbent = best.get(key)
        if incumbent is None:
            best[key] = record
            continue
        challenger_rank = (
            confidence_rank(record),
            record.get("score", 0),
            record.get("evidence_count", 0),
        )
        incumbent_rank = (
            confidence_rank(incumbent),
            incumbent.get("score", 0),
            incumbent.get("evidence_count", 0),
        )
        if challenger_rank > incumbent_rank:
            best[key] = record
    return list(best.values())


def pending_records(root: Path) -> list[dict[str, Any]]:
    state = load_state(root)
    reviewed = state.get("items", {})
    records = []
    for record in load_records(root):
        item_state = reviewed.get(record_key(record), {})
        if item_state.get("action") in TERMINAL_ACTIONS:
            continue
        records.append(record)
    records = dedup_by_content(records)
    # Highest-confidence-first; score already folds in confidence, evidence, recency.
    records.sort(
        key=lambda item: (confidence_rank(item), item.get("score", 0), item.get("last_seen", "")),
        reverse=True,
    )
    return records


def choose_record(records: list[dict[str, Any]], needle: str) -> dict[str, Any]:
    lowered = needle.lower()
    matches = [
        record
        for record in records
        if lowered in record.get("text", "").lower() or lowered in record_key(record).lower()
    ]
    if not matches:
        raise SystemExit(f"No pending candidate matched: {needle}")
    if len(matches) > 1:
        preview = "\n".join(f"- {record['candidate_type']}: {record['text']}" for record in matches[:8])
        raise SystemExit(f"Multiple candidates matched. Use a narrower needle:\n{preview}")
    return matches[0]


def append_review_log(path: Path, record: dict[str, Any], action: str, note: str | None = None) -> None:
    timestamp = now_iso()
    lines = [
        f"## {timestamp} {action}",
        "",
        f"- candidate_type: `{record['candidate_type']}`",
        f"- destination: `{record['suggested_destination']}`",
        f"- confidence: `{record['confidence']}`",
        f"- evidence_count: `{record['evidence_count']}`",
        f"- content: {record['text']}",
    ]
    if note:
        lines.append(f"- note: {note}")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")


def append_to_destination(root: Path, record: dict[str, Any], note: str | None = None) -> None:
    destination = root / record["suggested_destination"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
    if record["text"] in existing:
        return
    timestamp = now_iso()[:10]
    lines = [
        "",
        "## Reviewed Candidates",
        "",
        f"- [{timestamp}, reviewed] {record['text']}",
        f"  - source_sessions: `{', '.join(record.get('source_session_ids', []))}`",
        f"  - confidence: `{record.get('confidence')}`",
        f"  - rationale: {record.get('justification') or record.get('why_it_matters')}",
    ]
    if note:
        lines.append(f"  - review_note: {note}")
    lines.append("")
    appended = "\n".join(lines)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(appended)
    # Durable promotion (spec §3/§7): record to the Logbook with enough to undo.
    # prior_content restores the file exactly; appended_text isolates this write.
    logbook.record_change(
        root,
        actor="review.accept",
        action="append",
        path=destination,
        summary=f"promoted {record['candidate_type']} into {record['suggested_destination']}",
        reversal={
            "prior_content": existing,
            "appended_text": appended,
            "file_existed": destination_existed_before(existing, destination),
        },
        metadata={
            "candidate_type": record.get("candidate_type"),
            "confidence": record.get("confidence"),
            "content_hash": content_hash(record),
            "note": note,
        },
    )


def destination_existed_before(existing: str, destination: Path) -> bool:
    # ``existing`` is "" both when the file was absent and when it was empty.
    # The reversal only needs prior_content to restore; this flag is advisory.
    return bool(existing)


def append_decision_log(
    root: Path, record: dict[str, Any], action: str, note: str | None = None
) -> None:
    """Append one labeled (state, action) record for the future allocation policy (§7).

    Append-only JSONL, one decision per line. Fields are flat and stable so the log
    can be loaded directly as a training dataset later. `candidate_id` is the content
    hash, joinable across sessions; `content_hash` is kept explicit for forward-compat.
    """
    path = root / DECISIONS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    cid = content_hash(record)
    entry = {
        "timestamp": now_iso(),
        "candidate_id": cid,
        "content_hash": cid,
        "candidate_type": record.get("candidate_type"),
        "confidence": record.get("confidence"),
        "decision": DECISION_LABELS.get(action, action),
        "scope": record.get("suggested_destination", ""),
        "suggested_destination": record.get("suggested_destination", ""),
        "evidence_count": record.get("evidence_count"),
        "score": record.get("score"),
        "signal_ladder": record.get("signal_ladder"),
        "tier": record.get("tier"),
        "text": record.get("text"),
        "note": note,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True))
        handle.write("\n")


def record_decision(root: Path, record: dict[str, Any], action: str, note: str | None = None) -> None:
    state = load_state(root)
    state.setdefault("items", {})[record_key(record)] = {
        "action": action,
        "reviewed_at": now_iso(),
        "candidate_type": record["candidate_type"],
        "suggested_destination": record["suggested_destination"],
        "text": record["text"],
        "note": note,
    }
    save_state(root, state)
    append_decision_log(root, record, action, note)


def cmd_stats(root: Path) -> int:
    records = pending_records(root)
    by_type: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for record in records:
        by_type[record["candidate_type"]] = by_type.get(record["candidate_type"], 0) + 1
        by_tier[record.get("tier", "queue")] = by_tier.get(record.get("tier", "queue"), 0) + 1
    print(f"pending: {len(records)}")
    for candidate_type in sorted(by_type):
        print(f"type.{candidate_type}: {by_type[candidate_type]}")
    for tier in sorted(by_tier):
        print(f"tier.{tier}: {by_tier[tier]}")
    return 0


def cmd_list(root: Path, limit: int) -> int:
    records = pending_records(root)[:limit]
    if not records:
        print("No pending candidates.")
        return 0
    for record in records:
        print(
            f"{record['candidate_type']} | {record.get('tier', 'queue')} | "
            f"{record['confidence']} | {record['text']}"
        )
    return 0


def apply_action(root: Path, record: dict[str, Any], action: str, note: str | None) -> None:
    """Apply one terminal review action to a single record.

    accept  -> write to suggested_destination + reviewed-accepted log.
    reject  -> reviewed-rejected log only.
    expire  -> reviewed-expired log only.
    Nothing is ever deleted: the candidate source files stay, the decision is
    recorded in review-state (so it leaves the pending queue) and in the
    append-only decisions log. All reversible and auditable.
    """
    if action == "accepted":
        # append_to_destination records its own durable Logbook entry (the file write).
        append_to_destination(root, record, note)
        append_review_log(root / REVIEWED_ACCEPTED, record, "accepted", note)
    elif action == "rejected":
        append_review_log(root / REVIEWED_REJECTED, record, "rejected", note)
        record_candidate_status_flip(root, record, "rejected", note)
    elif action == "expired":
        append_review_log(root / REVIEWED_EXPIRED, record, "expired", note)
        record_candidate_status_flip(root, record, "expired", note)
    else:
        raise ValueError(f"Unsupported terminal action: {action}")
    record_decision(root, record, action, note)


def record_candidate_status_flip(
    root: Path, record: dict[str, Any], action: str, note: str | None
) -> None:
    """Record a candidate status flip (reject / expire) to the Logbook.

    No durable contract file changes here, but the candidate leaves the pending
    queue. The flip is reversible: clearing this candidate's entry in
    review-state.json returns it to pending. Evidence is never deleted.
    """
    logbook.record_change(
        root,
        actor=f"review.{DECISION_LABELS.get(action, action)}",
        action="candidate-status-flip",
        path=REVIEW_STATE,
        summary=f"{DECISION_LABELS.get(action, action)} candidate: {record['text'][:80]}",
        reversal={
            "review_state_key": record_key(record),
            "prior_action": "pending",
        },
        metadata={
            "candidate_type": record.get("candidate_type"),
            "content_hash": content_hash(record),
            "note": note,
        },
    )


def cmd_accept(root: Path, needle: str, note: str | None) -> int:
    record = choose_record(pending_records(root), needle)
    apply_action(root, record, "accepted", note)
    print(f"accepted: {record['text']}")
    return 0


def cmd_reject(root: Path, needle: str, note: str | None) -> int:
    record = choose_record(pending_records(root), needle)
    apply_action(root, record, "rejected", note)
    print(f"rejected: {record['text']}")
    return 0


def cmd_defer(root: Path, needle: str, note: str | None) -> int:
    record = choose_record(pending_records(root), needle)
    state = load_state(root)
    key = record_key(record)
    existing = state.setdefault("items", {}).get(key, {})
    state["items"][key] = {
        "action": "deferred",
        "reviewed_at": now_iso(),
        "candidate_type": record["candidate_type"],
        "suggested_destination": record["suggested_destination"],
        "text": record["text"],
        "note": note,
        "defer_count": int(existing.get("defer_count", 0)) + 1,
    }
    save_state(root, state)
    append_decision_log(root, record, "deferred", note)
    print(f"deferred: {record['text']}")
    return 0


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def expirable_records(root: Path, days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result: list[dict[str, Any]] = []
    for record in pending_records(root):
        last_seen = parse_iso(record.get("last_seen"))
        if last_seen and last_seen < cutoff:
            result.append(record)
    return result


def cmd_expire(root: Path, days: int, apply: bool) -> int:
    records = expirable_records(root, days)
    if not apply:
        for record in records:
            print(f"would expire: {record['candidate_type']} | {record['last_seen']} | {record['text']}")
        print(f"expirable: {len(records)}")
        return 1 if records else 0
    for record in records:
        apply_action(root, record, "expired", f"older than {days} days")
    print(f"expired: {len(records)}")
    return 0


ACTION_VERBS = {"accept": "accepted", "reject": "rejected", "expire": "expired"}


def cmd_batch(
    root: Path,
    action: str,
    needles: list[str] | None = None,
    top: int | None = None,
    note: str | None = None,
) -> int:
    """Apply one terminal action to many candidates in a single pass.

    Selection is either an explicit list of needles, or `top=N` (the N
    highest-confidence-first pending candidates). Records are resolved against a
    single snapshot so ordering and dedup are consistent across the batch.
    """
    terminal = ACTION_VERBS.get(action)
    if terminal is None:
        raise SystemExit(f"Unsupported batch action: {action}")

    snapshot = pending_records(root)
    selected: list[dict[str, Any]] = []
    if top is not None:
        selected = snapshot[:top]
    if needles:
        for needle in needles:
            selected.append(choose_record(snapshot, needle))

    # Dedup the selection by content so a record is never actioned twice.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in selected:
        cid = content_hash(record)
        if cid in seen:
            continue
        seen.add(cid)
        unique.append(record)

    if not unique:
        print("No candidates selected.")
        return 0

    for record in unique:
        apply_action(root, record, terminal, note)
        print(f"{action}: {record['text']}")
    print(f"{action} total: {len(unique)}")
    return 0


def load_focus_terms(root: Path) -> list[str]:
    """Pull content words from the root STATE.md Current Focus block.

    Used by triage to surface the few pending candidates that touch what the user is
    actively working on, instead of dropping everything blindly.
    """
    state_path = root / "STATE.md"
    if not state_path.exists():
        return []
    text = state_path.read_text(encoding="utf-8")
    focus_lines: list[str] = []
    capture = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## current focus"):
            capture = True
            continue
        if capture and stripped.startswith("## "):
            break
        if capture:
            focus_lines.append(stripped)
    blob = " ".join(focus_lines).lower()
    # Generic words that match almost any candidate and so make focus useless as a filter.
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "into", "next",
        "still", "now", "are", "was", "were", "has", "have", "its", "but",
        "not", "yet", "out", "all", "one", "two", "make", "making", "whether",
        "continue", "focus", "loop", "decide", "apply", "exist", "files",
        "checks", "support", "clean", "session", "review", "tests", "reduce",
        "behaviour", "reporting", "operationally", "whether", "should", "raw",
        "item", "items", "scenario", "current",
    }
    terms = {
        word.strip(".,:;()`[]*")
        for word in blob.split()
        if len(word.strip(".,:;()`[]*")) >= 5 and not any(ch.isdigit() for ch in word)
    }
    return sorted(t for t in terms if t and t not in stop)


def matches_focus(record: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return False
    text = record.get("text", "").lower()
    return any(term in text for term in terms)


def triage(root: Path, days: int = 30, apply: bool = False) -> dict[str, Any]:
    """One-time backlog triage.

    Bulk-expires stale candidates (last_seen older than `days`), surfaces the few
    that match the current STATE.md focus, and reports what would be dropped by
    reason. Dry-run by default (writes nothing). Reuses the expire/dedup machinery;
    accepts are never automated — a human still decides those.
    """
    pending = pending_records(root)
    pending_before = len(pending)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    focus_terms = load_focus_terms(root)

    stale: list[dict[str, Any]] = []
    focus_hits: list[dict[str, Any]] = []
    by_reason: dict[str, int] = {}
    by_type: dict[str, int] = {}

    for record in pending:
        last_seen = parse_iso(record.get("last_seen"))
        is_stale = bool(last_seen and last_seen < cutoff)
        # A candidate older than the window is stale regardless of focus: the focus
        # block itself can be stale, so we never keep a stale item just because a
        # generic word matched. Stale gets expired; focus is surfaced among the
        # *fresh* set so the human sees what's still live and decides the accepts.
        if is_stale:
            stale.append(record)
            by_reason["stale"] = by_reason.get("stale", 0) + 1
            ctype = record.get("candidate_type", "unknown")
            by_type[ctype] = by_type.get(ctype, 0) + 1
        elif matches_focus(record, focus_terms):
            focus_hits.append(record)

    report = {
        "pending_before": pending_before,
        "would_expire": len(stale),
        "resulting_pending": pending_before - len(stale),
        "by_reason": by_reason,
        "by_type": by_type,
        "focus_terms": focus_terms,
        "focus_hits": [r.get("text", "") for r in focus_hits[:20]],
        "focus_hit_count": len(focus_hits),
        "applied": apply,
        "days": days,
    }

    if apply:
        for record in stale:
            apply_action(root, record, "expired", f"triage: stale (>{days}d), not in current focus")
        report["expired"] = len(stale)
        report["resulting_pending"] = len(pending_records(root))

    return report


def print_triage_report(report: dict[str, Any]) -> None:
    mode = "APPLIED" if report["applied"] else "DRY-RUN"
    print(f"=== Backlog triage ({mode}) ===")
    print(f"pending before: {report['pending_before']}")
    print(f"would expire (stale > {report['days']}d, not in focus): {report['would_expire']}")
    print(f"resulting pending: {report['resulting_pending']}")
    print("by reason:")
    for reason, count in sorted(report["by_reason"].items()):
        print(f"  {reason}: {count}")
    print("by type (expired):")
    for ctype, count in sorted(report["by_type"].items()):
        print(f"  {ctype}: {count}")
    print(f"focus terms: {', '.join(report['focus_terms']) or '(none found in STATE.md)'}")
    print(f"focus matches kept ({report['focus_hit_count']}):")
    for text in report["focus_hits"]:
        print(f"  - {text}")
    if not report["applied"]:
        print("\nNothing written. Re-run with --apply to expire the stale set.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review ExoCortex promotion candidates.")
    parser.add_argument("--root", default=None, help="ExoCortex repository root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("stats", help="Print pending candidate counts.")
    list_parser = subparsers.add_parser("list", help="List pending candidates.")
    list_parser.add_argument("--limit", type=int, default=20)

    accept = subparsers.add_parser("accept", help="Accept a pending candidate by text match.")
    accept.add_argument("needle")
    accept.add_argument("--note", default=None)

    reject = subparsers.add_parser("reject", help="Reject a pending candidate by text match.")
    reject.add_argument("needle")
    reject.add_argument("--note", default=None)

    defer = subparsers.add_parser("defer", help="Defer a pending candidate without removing it.")
    defer.add_argument("needle")
    defer.add_argument("--note", default=None)

    expire = subparsers.add_parser("expire", help="Expire old pending candidates.")
    expire.add_argument("--days", type=int, default=30)
    expire.add_argument("--apply", action="store_true")

    batch = subparsers.add_parser(
        "batch", help="Apply one action (accept/reject/expire) to many candidates."
    )
    batch.add_argument("action", choices=sorted(ACTION_VERBS))
    batch.add_argument(
        "needles", nargs="*", help="Text matches; one record per needle."
    )
    batch.add_argument(
        "--top", type=int, default=None, help="Select the N highest-confidence pending candidates."
    )
    batch.add_argument("--note", default=None)

    triage_parser = subparsers.add_parser(
        "triage", help="One-time backlog triage: expire stale, surface focus matches."
    )
    triage_parser.add_argument("--days", type=int, default=30)
    triage_parser.add_argument(
        "--apply", action="store_true", help="Actually expire (default: dry-run)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)
    if args.command == "stats":
        return cmd_stats(root)
    if args.command == "list":
        return cmd_list(root, args.limit)
    if args.command == "accept":
        return cmd_accept(root, args.needle, args.note)
    if args.command == "reject":
        return cmd_reject(root, args.needle, args.note)
    if args.command == "defer":
        return cmd_defer(root, args.needle, args.note)
    if args.command == "expire":
        return cmd_expire(root, args.days, args.apply)
    if args.command == "batch":
        return cmd_batch(root, args.action, args.needles or None, args.top, args.note)
    if args.command == "triage":
        report = triage(root, days=args.days, apply=args.apply)
        print_triage_report(report)
        return 0
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
