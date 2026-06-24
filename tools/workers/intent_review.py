#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INTENT_REVIEW_STATE_VERSION = 1
INTENT_STAGES = {
    "inferred_intent",
    "confirmed_open_loop",
    "priority",
    "rejected",
}
STRONG_COMMITMENT_RE = re.compile(
    r"\b(i will|we will|i'll|we'll|i need to|we need to|must|have to|going to|plan to|commit to)\b",
    re.I,
)
URGENT_INTENT_RE = re.compile(
    r"\b(now|next|soon|asap|this week|today|tomorrow|urgent|by [a-z0-9-]+)\b",
    re.I,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def review_state_path(root: Path) -> Path:
    return root / "journal" / "inbox" / "intent-review-state.json"


def reviewed_intents_path(root: Path) -> Path:
    return root / "journal" / "inbox" / "reviewed-intents.md"


def empty_review_state() -> dict[str, Any]:
    return {
        "version": INTENT_REVIEW_STATE_VERSION,
        "updated_at": None,
        "items": {},
    }


def load_review_state(root: Path) -> dict[str, Any]:
    path = review_state_path(root)
    if not path.exists():
        return empty_review_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return empty_review_state()
    payload.setdefault("version", INTENT_REVIEW_STATE_VERSION)
    payload.setdefault("updated_at", None)
    payload.setdefault("items", {})
    return payload


def save_review_state(root: Path, payload: dict[str, Any]) -> None:
    path = review_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["version"] = INTENT_REVIEW_STATE_VERSION
    payload["updated_at"] = now_iso()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def intent_record_key(record: dict[str, Any]) -> str:
    return f"{record['normalized_key']}::{record['suggested_destination']}"


def commitment_strength(text: str) -> str:
    return "strong" if STRONG_COMMITMENT_RE.search(text or "") else "soft"


def is_urgent(text: str) -> bool:
    return bool(URGENT_INTENT_RE.search(text or ""))


def default_intent_stage(record: dict[str, Any]) -> str:
    return "inferred_intent" if record.get("candidate_type") == "intent" else record.get("signal_ladder", "candidate")


def review_recommendation(record: dict[str, Any]) -> str:
    stage = record.get("intent_stage", default_intent_stage(record))
    if stage == "priority":
        return "reviewed"
    if stage == "rejected":
        return "reviewed"
    if stage == "confirmed_open_loop":
        if record.get("evidence_count", 1) >= 3 or is_urgent(record.get("text", "")):
            return "promote_priority"
        return "tracked_open_loop"
    if (
        record.get("confidence") == "high"
        or record.get("evidence_count", 1) >= 2
        or commitment_strength(record.get("text", "")) == "strong"
    ):
        return "confirm_open_loop"
    return "keep_inferred"


def annotate_records(records: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    state = load_review_state(root).get("items", {})
    annotated: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        if item.get("candidate_type") != "intent":
            annotated.append(item)
            continue

        review_state = state.get(intent_record_key(item), {})
        stage = review_state.get("stage", "inferred_intent")
        if stage not in INTENT_STAGES:
            stage = "inferred_intent"
        item["intent_stage"] = stage
        item["commitment_strength"] = commitment_strength(item.get("text", ""))
        item["review_recommendation"] = review_recommendation(item)
        if review_state:
            item["reviewed_at"] = review_state.get("reviewed_at")
            item["review_note"] = review_state.get("review_note")
            item["promoted_to"] = review_state.get("promoted_to")
            item["promotion_text"] = review_state.get("promotion_text")
        annotated.append(item)
    return annotated


def pending_intents(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in records
        if item.get("candidate_type") == "intent" and item.get("intent_stage") == "inferred_intent"
    ]


def reviewed_intents(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in records
        if item.get("candidate_type") == "intent" and item.get("intent_stage") in {"confirmed_open_loop", "priority", "rejected"}
    ]


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def render_reviewed_intents(records: list[dict[str, Any]]) -> str:
    sections = (
        ("## Confirmed Open Loops", "confirmed_open_loop"),
        ("## Priorities", "priority"),
        ("## Rejected", "rejected"),
    )
    lines = [
        "# Reviewed Intent Outcomes",
        "",
        "Generated from inferred-intent review decisions.",
        "",
    ]
    for heading, stage in sections:
        lines.append(heading)
        lines.append("")
        items = [item for item in records if item.get("intent_stage") == stage]
        if not items:
            lines.extend(["- None recorded.", ""])
            continue
        for item in items:
            lines.extend(
                [
                    f"### {item.get('promotion_text') or item['text']}",
                    "",
                    f"- intent_stage: `{item['intent_stage']}`",
                    f"- evidence_count: `{item['evidence_count']}`",
                    f"- confidence: `{item['confidence']}`",
                    f"- suggested_destination: `{item['suggested_destination']}`",
                    f"- commitment_strength: `{item['commitment_strength']}`",
                ]
            )
            if item.get("promoted_to"):
                lines.append(f"- promoted_to: `{item['promoted_to']}`")
            if item.get("reviewed_at"):
                lines.append(f"- reviewed_at: `{item['reviewed_at']}`")
            if item.get("review_note"):
                lines.append(f"- review_note: {item['review_note']}")
            lines.append("- recent_evidence:")
            lines.extend(f"  - {evidence}" for evidence in item.get("recent_evidence", []))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_reviewed_intents(root: Path, records: list[dict[str, Any]]) -> None:
    _write_text(reviewed_intents_path(root), render_reviewed_intents(records))


def _default_title(path: Path) -> str:
    return path.stem.replace("_", " ")


def _ensure_heading(path: Path, heading: str) -> str:
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = f"# {_default_title(path)}\n\n"
        text += f"{heading}\n\n"
    return text


def _append_section_block(path: Path, heading: str, block: str, marker: str) -> None:
    text = _ensure_heading(path, heading)
    if marker in text:
        return
    if heading not in text:
        text = text.rstrip() + f"\n\n{heading}\n\n"
    else:
        text = text.rstrip() + "\n\n"
    text += block.rstrip() + "\n"
    _write_text(path, text)


def record_review_decision(
    root: Path,
    record: dict[str, Any],
    stage: str,
    promoted_to: str | None = None,
    review_note: str | None = None,
    promotion_text: str | None = None,
) -> dict[str, Any]:
    if stage not in INTENT_STAGES:
        raise ValueError(f"Unsupported intent stage: {stage}")
    payload = load_review_state(root)
    reviewed_at = now_iso()
    payload["items"][intent_record_key(record)] = {
        "stage": stage,
        "reviewed_at": reviewed_at,
        "review_note": review_note,
        "promoted_to": promoted_to,
        "promotion_text": promotion_text,
    }
    save_review_state(root, payload)
    return payload["items"][intent_record_key(record)]


def append_open_loop(root: Path, record: dict[str, Any], text: str, review_note: str | None = None) -> None:
    path = root / "system" / "OPEN LOOPS.md"
    key = intent_record_key(record)
    block_lines = [
        f"### {text}",
        "",
        f"- intent_key: `{key}`",
        "- promoted_from: `inferred_intent`",
        f"- promoted_at: `{now_iso()}`",
        f"- evidence_count: `{record['evidence_count']}`",
        f"- confidence: `{record['confidence']}`",
        f"- source_sessions: `{', '.join(record.get('source_session_ids', []))}`",
        "- recent_evidence:",
    ]
    block_lines.extend(f"  - {item}" for item in record.get("recent_evidence", []))
    if review_note:
        block_lines.append(f"- review_note: {review_note}")
    block = "\n".join(block_lines) + "\n"
    _append_section_block(path, "## Reviewed Intent Promotions", block, f"- intent_key: `{key}`")


def append_priority(root: Path, record: dict[str, Any], text: str, review_note: str | None = None) -> None:
    path = root / "system" / "PRIORITIES.md"
    key = intent_record_key(record)
    block_lines = [
        f"### {text}",
        "",
        f"- intent_key: `{key}`",
        "- promoted_from: `confirmed_open_loop`",
        f"- promoted_at: `{now_iso()}`",
        f"- evidence_count: `{record['evidence_count']}`",
        f"- confidence: `{record['confidence']}`",
        f"- source_sessions: `{', '.join(record.get('source_session_ids', []))}`",
        "- recent_evidence:",
    ]
    block_lines.extend(f"  - {item}" for item in record.get("recent_evidence", []))
    if review_note:
        block_lines.append(f"- review_note: {review_note}")
    block = "\n".join(block_lines) + "\n"
    _append_section_block(path, "## Reviewed Intent Promotions", block, f"- intent_key: `{key}`")


def choose_record(records: list[dict[str, Any]], needle: str) -> dict[str, Any]:
    normalized = needle.strip().lower()
    matches = [
        item
        for item in records
        if normalized in item.get("text", "").lower() or normalized in item.get("normalized_key", "")
    ]
    if not matches:
        raise ValueError(f"No intent matched '{needle}'.")
    if len(matches) > 1:
        options = "; ".join(item["text"] for item in matches[:5])
        raise ValueError(f"Multiple intents matched '{needle}': {options}")
    return matches[0]


def default_promotion_text(text: str) -> str:
    cleaned = text.strip().rstrip(".")
    cleaned = re.sub(r"^(we|i)\s+(will|should|need to|have to|plan to|want to)\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^(later|eventually|at some point)\s+", "", cleaned, flags=re.I)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else text


def load_reviewable_records(root: Path) -> list[dict[str, Any]]:
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.workers import process_session as worker

    aggregated = worker.aggregate_candidate_records(worker.load_candidate_records(root))
    return annotate_records(aggregated, root)


def _list_command(root: Path) -> int:
    records = load_reviewable_records(root)
    pending = pending_intents(records)
    reviewed = reviewed_intents(records)

    print("Pending inferred intents:")
    if not pending:
        print("- none")
    for item in pending:
        print(
            f"- {item['text']} | recommendation={item['review_recommendation']} | evidence={item['evidence_count']} | confidence={item['confidence']}"
        )
    print("")
    print("Reviewed intents:")
    if not reviewed:
        print("- none")
    for item in reviewed:
        print(
            f"- {item.get('promotion_text') or item['text']} | stage={item['intent_stage']} | promoted_to={item.get('promoted_to') or 'n/a'}"
        )
    return 0


def _confirm_command(root: Path, needle: str, text: str | None, note: str | None) -> int:
    record = choose_record(load_reviewable_records(root), needle)
    promotion_text = text or default_promotion_text(record["text"])
    append_open_loop(root, record, promotion_text, note)
    record_review_decision(
        root,
        record,
        stage="confirmed_open_loop",
        promoted_to="system/OPEN LOOPS.md",
        review_note=note,
        promotion_text=promotion_text,
    )
    write_reviewed_intents(root, reviewed_intents(load_reviewable_records(root)))
    print(f"Confirmed open loop: {promotion_text}")
    return 0


def _prioritize_command(root: Path, needle: str, text: str | None, note: str | None) -> int:
    record = choose_record(load_reviewable_records(root), needle)
    promotion_text = text or default_promotion_text(record.get("promotion_text") or record["text"])
    append_priority(root, record, promotion_text, note)
    record_review_decision(
        root,
        record,
        stage="priority",
        promoted_to="system/PRIORITIES.md",
        review_note=note,
        promotion_text=promotion_text,
    )
    write_reviewed_intents(root, reviewed_intents(load_reviewable_records(root)))
    print(f"Promoted priority: {promotion_text}")
    return 0


def _reject_command(root: Path, needle: str, note: str | None) -> int:
    record = choose_record(load_reviewable_records(root), needle)
    record_review_decision(
        root,
        record,
        stage="rejected",
        promoted_to=None,
        review_note=note,
        promotion_text=record["text"],
    )
    write_reviewed_intents(root, reviewed_intents(load_reviewable_records(root)))
    print(f"Rejected intent: {record['text']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review and promote inferred ExoCortex intents.")
    parser.add_argument("--root", default=None, help="ExoCortex repo root. Defaults to the script's repo root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List pending and reviewed inferred intents.")

    confirm = subparsers.add_parser("confirm-open-loop", help="Confirm an inferred intent into system/OPEN LOOPS.md.")
    confirm.add_argument("needle", help="Unique text fragment matching the intent.")
    confirm.add_argument("--text", default=None, help="Override the durable open-loop text.")
    confirm.add_argument("--note", default=None, help="Optional review note.")

    priority = subparsers.add_parser("promote-priority", help="Promote a confirmed intent into system/PRIORITIES.md.")
    priority.add_argument("needle", help="Unique text fragment matching the intent.")
    priority.add_argument("--text", default=None, help="Override the durable priority text.")
    priority.add_argument("--note", default=None, help="Optional review note.")

    reject = subparsers.add_parser("reject", help="Reject an inferred intent and keep the decision trail.")
    reject.add_argument("needle", help="Unique text fragment matching the intent.")
    reject.add_argument("--note", default=None, help="Optional rejection note.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]

    if args.command == "list":
        return _list_command(root)
    if args.command == "confirm-open-loop":
        return _confirm_command(root, args.needle, args.text, args.note)
    if args.command == "promote-priority":
        return _prioritize_command(root, args.needle, args.text, args.note)
    if args.command == "reject":
        return _reject_command(root, args.needle, args.note)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
