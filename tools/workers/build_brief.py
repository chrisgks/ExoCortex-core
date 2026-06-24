#!/usr/bin/env python3
"""Assemble the Brief — the one ExoCortex surface that is actually read at startup.

The Brief answers, in one read:

- **What changed** since the last session (recent session/synthesis activity).
- **What's stale / needs attention** (STATE staleness, pending-queue size, raw
  backlog, synthesis errors) — reuses the existing health + hygiene signals.
- **What's queued for your decision** (the surface-now items + the few
  highest-confidence pending candidates from the review loop).
- **Next best moves** (1-3, from the Allocator — the transparent weighted-score
  next-best-move engine; same output as `exocortex-next`).
- **What's ready to ship** (from the Ship tracker, the output arm).

It also pulls a one-line pointer to the latest periodic synthesis so those
weekly/monthly/quarterly reports stop living as orphan unread files.

Contract (background layer): this generator **observes** state
and writes **only** the Brief file. It never durably promotes, never rewrites any
other file. Regeneration is cheap and idempotent.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root

from tools.workers import allocator, context_hygiene, health_check, review, reward_log, ship_tracker

BRIEF_FILE = Path("journal/inbox/brief.md")
SURFACE_NOW_FILE = Path("journal/inbox/surface-now.md")
SYNTHESIS_ERRORS_FILE = Path("journal/inbox/synthesis-errors.md")
PERIOD_DIRS = (("week", "weekly"), ("month", "monthly"), ("quarter", "quarterly"))
GENERATED_AT_PREFIX = "_Generated"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def strip_volatile(text: str) -> str:
    """Drop the only run-to-run-volatile line so two builds compare equal.

    The Brief is idempotent in content; only its generated-at stamp moves.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.startswith(GENERATED_AT_PREFIX)
    )


# --- What changed ---------------------------------------------------------


def recent_sessions(root: Path, limit: int = 5) -> list[str]:
    """Most recent session manifests, newest first, as `date / agent` lines."""
    manifests = sorted(
        (root / "journal" / "sessions").glob("*/*.json"), reverse=True
    )
    lines: list[str] = []
    for path in manifests:
        # Skip the sidecar artifacts (.intelligence.json etc.); keep bare manifests.
        if path.name.count(".") > 1:
            continue
        day = path.parent.name
        agent = ""
        try:
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
            agent = data.get("active_agent", "")
        except Exception:
            pass
        label = f"{day}" + (f" - {agent}" if agent else "")
        if label not in lines:
            lines.append(label)
        if len(lines) >= limit:
            break
    return lines


def what_changed(root: Path) -> list[str]:
    sessions = recent_sessions(root)
    if sessions:
        lines = [f"- Recent sessions: {', '.join(sessions)}"]
    else:
        lines = ["- No recent session activity captured."]
    pointer = latest_period_pointer(root)
    if pointer:
        lines.append(f"- Latest periodic synthesis: {pointer}")
    return lines


# --- Latest periodic synthesis pointer ------------------------------------


def latest_period_pointer(root: Path) -> str | None:
    """One-line pointer to the most recent period synthesis across cadences.

    Prefers the finest cadence present (week > month > quarter) since that is
    the freshest readable rollup. Routes the report into the Brief instead of
    leaving it orphaned.
    """
    for _level, dirname in PERIOD_DIRS:
        directory = root / "journal" / dirname
        if not directory.is_dir():
            continue
        reports = sorted(
            (p for p in directory.glob("*.md") if p.name != "README.md"), reverse=True
        )
        if reports:
            newest = reports[0]
            rel = newest.relative_to(root)
            return f"{newest.stem} (`{rel}`)"
    return None


# --- What's stale / needs attention ---------------------------------------


def stale_findings(root: Path) -> list[str]:
    """STATE staleness drawn from the existing hygiene checks (don't recompute)."""
    findings = context_hygiene.check_state_files(root, context_hygiene.DEFAULT_STALE_DAYS)
    lines: list[str] = []
    for finding in findings:
        lines.append(f"- {finding.severity}: {finding.path} - {finding.message}")
    return lines


# Health items the Brief does not surface. The Logbook is a write-tracking
# audit surface, not a next-action signal; keep it out of the Brief.
HEALTH_ITEMS_OMITTED = {"logbook"}


def health_lines(root: Path) -> list[str]:
    """Reuse the health_check signals: queue size, raw backlog, artifacts, etc."""
    items = health_check.build_health(root)
    lines: list[str] = []
    for item in items:
        if item.name in HEALTH_ITEMS_OMITTED:
            continue
        lines.append(f"- {item.status}: {item.name} - {item.detail}")
    return lines


def synthesis_error_note(root: Path) -> str | None:
    path = root / SYNTHESIS_ERRORS_FILE
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    count = sum(1 for line in text.splitlines() if line.startswith("## "))
    if count == 0:
        return None
    return f"- warn: synthesis errors logged ({count}); see `{SYNTHESIS_ERRORS_FILE}`"


def whats_stale(root: Path, max_state_lines: int = 8) -> list[str]:
    lines = health_lines(root)
    # STATE staleness can list many scopes; keep the Brief scannable by capping
    # the per-scope lines and noting the overflow rather than dumping all of them.
    state_lines = stale_findings(root)
    if len(state_lines) > max_state_lines:
        overflow = len(state_lines) - max_state_lines
        lines.extend(state_lines[:max_state_lines])
        lines.append(f"- info: +{overflow} more STATE.md scope(s) flagged (run `exocortex-health`)")
    else:
        lines.extend(state_lines)
    note = synthesis_error_note(root)
    if note:
        lines.append(note)
    return lines or ["- Nothing flagged."]


# --- What's queued for your decision --------------------------------------

SURFACE_HEADING_RE = re.compile(r"^### (.+)$")
SURFACE_SECTION_RE = re.compile(r"^## .* surface-now")


def latest_surface_now_items(root: Path, limit: int = 6) -> list[str]:
    """Headings (the candidate text) from the most recent surface-now section.

    surface-now.md is append-only with newest sections at the bottom. We take
    the last section's `### ...` headings, which are the items routed to the
    human for attention.
    """
    path = root / SURFACE_NOW_FILE
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = text.splitlines()
    # Find the start of the last surface-now section.
    last_section_start = None
    for index, line in enumerate(lines):
        if SURFACE_SECTION_RE.match(line):
            last_section_start = index
    if last_section_start is None:
        return []
    items: list[str] = []
    for line in lines[last_section_start:]:
        match = SURFACE_HEADING_RE.match(line)
        if match:
            items.append(match.group(1).strip())
    return items[:limit]


def top_pending(root: Path, limit: int = 3) -> list[str]:
    """The few highest-confidence pending candidates from the review loop."""
    try:
        records = review.pending_records(root)
    except Exception:
        return []
    lines: list[str] = []
    for record in records[:limit]:
        lines.append(
            f"- {record.get('confidence', '?')} | {record.get('candidate_type', '?')} | "
            f"{record.get('text', '')}"
        )
    return lines


def pending_checkin_line(root: Path) -> str | None:
    """One-line nudge when recent sessions are not yet rated.

    Sessions that could not prompt at close (Stop hook, desktop, non-tty) defer a
    check-in; this surfaces the count so they can be rated at next startup.
    Omitted entirely when there are none — never nag.
    """
    count = reward_log.pending_count(root)
    if count <= 0:
        return None
    word = "session" if count == 1 else "sessions"
    return (
        f"- {count} recent {word} not yet rated "
        f"(rate with `exocortex-checkin`)."
    )


def whats_queued(root: Path) -> list[str]:
    lines: list[str] = []
    surfaced = latest_surface_now_items(root)
    if surfaced:
        lines.append("Surfaced for your attention:")
        lines.extend(f"- {item}" for item in surfaced)
    pending = top_pending(root)
    if pending:
        lines.append("")
        lines.append("Top pending candidates (review with `exocortex-review`):")
        lines.extend(pending)
    checkin = pending_checkin_line(root)
    if checkin:
        lines.append("")
        lines.append(checkin)
    if not lines:
        return ["- Nothing queued."]
    return lines


# --- Next best moves (the Allocator) ---------------------------------------


def next_best_moves(root: Path) -> list[str]:
    """The Allocator's top 1-3 moves, each with its one-line why.

    Delegates to the Allocator (`tools/workers/allocator.py`) — the transparent
    weighted-score next-best-move engine. The same output is available on demand
    via `exocortex-next`. The Allocator is observe-only; surfacing it in the Brief
    must not log a suggestion (the Brief regenerates often and idempotently), so
    we read without logging here.
    """
    moves = allocator.propose(root, log=False)
    lines: list[str] = []
    for index, move in enumerate(moves, start=1):
        lines.append(f"{index}. {move['move']}")
        lines.append(f"   why: {move['why']}")
    return lines


# --- What's ready to ship (the Ship tracker, the output arm) ---------------

# A captured thread untouched for this many days is nudged as "going stale" —
# the capture-without-closure problem the output arm exists to counter.
SHIP_STALE_DAYS = 14

SHIP_STATUS_ORDER = ("captured", "shaped", "shipped")


def _ship_age_days(item: dict[str, Any]) -> float | None:
    raw = item.get("updated") or item.get("created")
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400.0


def _ship_item_line(item: dict[str, Any], *, flag_stale: bool = False) -> str:
    income = " [priority]" if item.get("income") else ""
    label = f"- {item.get('title', '?')} ({item.get('channel', 'other')}{income})"
    if flag_stale:
        age = _ship_age_days(item)
        days = f" — captured {int(age)}d ago, finish or drop it" if age is not None else ""
        label += days
    return label


def whats_ready_to_ship(root: Path) -> list[str]:
    """Render the Ship tracker grouped by status, nudging stale captured items.

    Omits gracefully (a single line) when there are no items. Highlights anything
    ``captured`` that has gone stale, since the output arm exists to push
    finishing/shipping over starting yet another thread.
    """
    items = ship_tracker.load_ship_items(root)
    if not items:
        return [
            "- Nothing in the Ship tracker yet "
            "(add with `exocortex-ship add \"<title>\"`)."
        ]

    grouped: dict[str, list[dict[str, Any]]] = {s: [] for s in SHIP_STATUS_ORDER}
    for item in items:
        grouped.setdefault(item.get("status", "captured"), []).append(item)

    lines: list[str] = []

    stale = [
        i
        for i in grouped.get("captured", [])
        if (_ship_age_days(i) or 0) >= SHIP_STALE_DAYS
    ]
    if stale:
        lines.append(f"Going stale — {len(stale)} captured thread(s) need closure:")
        for item in stale:
            lines.append(_ship_item_line(item, flag_stale=True))
        lines.append("")

    for status in SHIP_STATUS_ORDER:
        group = grouped.get(status, [])
        if not group:
            continue
        lines.append(f"{status} ({len(group)}):")
        for item in group:
            lines.append(_ship_item_line(item))
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()
    return lines


# --- Assembly -------------------------------------------------------------


def render_brief(root: Path) -> str:
    root = root.resolve()
    sections: list[str] = [
        "# ExoCortex Brief",
        "",
        f"{GENERATED_AT_PREFIX} {now_iso()}_",
        "",
        "## What changed",
        "",
        *what_changed(root),
        "",
        "## What's stale / needs attention",
        "",
        *whats_stale(root),
        "",
        "## What's queued for your decision",
        "",
        *whats_queued(root),
        "",
        "## Next best moves",
        "",
        "_From the Allocator (`exocortex-next`). Transparent weighted score; `--why` shows the breakdown._",
        "",
        *next_best_moves(root),
        "",
        "## What's ready to ship",
        "",
        "_The Ship tracker (`exocortex-ship`). Output arm — captured -> shaped -> shipped._",
        "",
        *whats_ready_to_ship(root),
        "",
    ]
    return "\n".join(sections)


def write_brief(root: Path) -> Path:
    root = root.resolve()
    path = root / BRIEF_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_brief(root), encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble the ExoCortex Brief.")
    parser.add_argument("--root", default=None)
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the Brief instead of writing it (writes nothing).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)
    if args.stdout:
        print(render_brief(root), end="")
        return 0
    path = write_brief(root)
    print(f"wrote {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
