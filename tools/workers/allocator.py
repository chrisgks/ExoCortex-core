#!/usr/bin/env python3
"""The Allocator — the next-best-move engine.

This is the "what now" engine. It reads current state and proposes the top 1-3
next moves, each with a one-line *why* and a transparent score breakdown. It
reads the signals the other pieces produce (staleness, the feedback log, the
Ship tracker).

Contract:

- **Read + propose only (observe).** The Allocator never takes actions, never
  promotes, never mutates any durable file. The *only* file it writes is its own
  suggestions log, ``journal/allocations.jsonl``.
- **Transparent weighted score over a DECLARED feature space.** Every candidate
  move is scored as a documented weighted sum over a *named, fixed* feature set
  (``FEATURE_SCHEMA`` + ``WEIGHTS`` below). The scoring policy is the pure
  function ``score(features) -> float``, deliberately isolated from the gathering
  code so a learned / federated policy can replace it later **without
  re-architecting** — same feature vector in, a score out.
- **Logs every suggestion as training data.** Each ``propose`` run appends
  one record per suggestion to ``journal/allocations.jsonl``: timestamp, id, the
  full feature vector, the per-feature score breakdown, the proposed move, and
  blank ``taken`` / ``reward`` slots. Joinable by ``id`` with ``reward-log.jsonl``
  and ``review-decisions.jsonl`` — the substrate a future learned policy trains
  on.

------------------------------------------------------------------------------
THE FEDERATED-READINESS SEAM (read this before changing scoring)
------------------------------------------------------------------------------
``FEATURE_SCHEMA`` is the stable, named, ordered contract between *gathering*
(``gather_features``, which turns repo state into a feature vector per candidate)
and *policy* (``score``, the weighted sum). A learned or federated policy
replaces ``score`` alone; it consumes the same named feature dict and returns a
float. Nothing else changes. Do not let scoring logic leak into the gatherers,
and do not let gathering assumptions leak into ``score``. Keep them apart.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root

from tools.workers import context_hygiene, ship_tracker, reward_log, review

ALLOCATIONS_PATH = Path("journal/allocations.jsonl")

# A captured ship thread untouched this long is "going stale" — the
# capture-without-closure problem the output arm exists to counter. Matches the
# Brief's SHIP_STALE_DAYS so the two surfaces agree.
SHIP_STALE_DAYS = 14
# How many recent feedback rows define "recent rating".
RECENT_ENERGY_WINDOW = 5


# ===========================================================================
# THE DECLARED FEATURE SPACE  (the swap-for-learned-policy contract)
# ===========================================================================
#
# Named, fixed, ordered. Every candidate move is described by exactly these
# features, each normalised to roughly [0, 1] (a couple can exceed 1 when a
# backlog is far over its limit; the weights keep that bounded in practice).
#
#   staleness            — the live STATE.md is stale / missing its focus; the
#                          system can't decide well until it's refreshed.
#   deadline_pressure    — an explicit, time-bound commitment is in play.
#   energy_match         — how well this move fits the *current* available
#                          capacity. Light moves score up when the recent rating
#                          is low; heavier moves score up when it is high.
#   shippable_unfinished — a captured-but-unshipped thread is going stale; finish
#                          and ship it (counters capture-without-closure).
#   surfaced_priority    — items already surfaced for the human's attention.
#   queue_pressure       — review backlog + raw inbox over their thresholds.
#
FEATURE_SCHEMA: tuple[str, ...] = (
    "staleness",
    "deadline_pressure",
    "energy_match",
    "shippable_unfinished",
    "surfaced_priority",
    "queue_pressure",
)

# The weights ARE the v1 policy. Inspectable, swappable. A learned/federated
# policy replaces ``score`` (below); these weights are its hand-coded stand-in.
WEIGHTS: dict[str, float] = {
    "staleness": 2.5,
    "deadline_pressure": 3.0,
    "energy_match": 1.0,
    "shippable_unfinished": 2.0,
    "surfaced_priority": 1.5,
    "queue_pressure": 1.2,
}


def empty_features() -> dict[str, float]:
    """A zeroed feature vector with the full, stable schema."""
    return {name: 0.0 for name in FEATURE_SCHEMA}


def breakdown(features: dict[str, float]) -> dict[str, float]:
    """Per-feature contribution (weight * value) — the inspectable score detail."""
    return {
        name: round(WEIGHTS[name] * float(features.get(name, 0.0)), 4)
        for name in FEATURE_SCHEMA
    }


def score(features: dict[str, float]) -> float:
    """The v1 policy: a transparent weighted sum over the declared feature space.

    THIS is the swappable seam. A learned / federated policy replaces this single
    pure function; it takes the same named feature dict and returns a float.
    Unknown keys are ignored and missing features default to 0.0, so the function
    survives schema evolution on either side of the seam.
    """
    return round(
        sum(WEIGHTS[name] * float(features.get(name, 0.0)) for name in FEATURE_SCHEMA),
        4,
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ===========================================================================
# GATHERING  (repo state -> signals -> candidate moves, scored)
# These functions know about the repo; they must NOT contain scoring policy.
# ===========================================================================


def _ship_age_days(item: dict[str, Any]) -> float:
    raw = item.get("updated") or item.get("created")
    if not raw:
        return 0.0
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - when).total_seconds() / 86400.0)


def recent_energy(root: Path) -> float | None:
    """Mean of the most recent answered ratings, or None if none answered.

    Reads the feedback log. Skipped/unanswered rows (the rating is null) are
    ignored — they carry no signal.
    """
    path = root / reward_log.REWARD_LOG_PATH
    if not path.exists():
        return None
    energies: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        value = row.get("energy")
        if isinstance(value, int):
            energies.append(value)
    if not energies:
        return None
    recent = energies[-RECENT_ENERGY_WINDOW:]
    return sum(recent) / len(recent)


def _energy_factor_for(weight: str, energy: float | None) -> float:
    """How well a move of a given *weight* fits the current rating, in [0, 1].

    ``weight`` is the move's load: "light" (small, low-friction) or "heavy"
    (deep, demanding). With no signal we return a neutral 0.5 so the feature
    never dominates blind. A low recent rating lifts light moves; a high recent
    rating lifts heavy moves. (The rating is 1-5.)
    """
    if energy is None:
        return 0.5
    # Map 1..5 -> 0..1 as "how much capacity is available".
    avail = (energy - 1.0) / 4.0
    if weight == "light":
        return round(1.0 - avail, 4)  # low rating -> light moves fit best
    return round(avail, 4)  # high rating -> heavy moves fit best


def _state_findings(root: Path) -> list[Any]:
    return context_hygiene.check_state_files(root, context_hygiene.DEFAULT_STALE_DAYS)


def _surfaced_count(root: Path) -> int:
    # Reuse the Brief's surface-now reader so the two surfaces agree.
    from tools.workers import build_brief

    return len(build_brief.latest_surface_now_items(root))


def _pending_count(root: Path) -> int:
    try:
        return len(review.pending_records(root))
    except Exception:
        return 0


def _raw_inbox_count(root: Path) -> int:
    raw = root / "raw" / "inbox"
    if not raw.exists():
        return 0
    return len(
        [
            p
            for p in raw.rglob("*")
            if p.is_file() and p.name != "README.md" and not context_hygiene.ignored(p)
        ]
    )


def _make_move(
    move: str, why: str, features: dict[str, float], *, kind: str
) -> dict[str, Any]:
    full = empty_features()
    full.update({k: float(v) for k, v in features.items() if k in FEATURE_SCHEMA})
    return {
        "id": uuid.uuid4().hex[:16],
        "kind": kind,
        "move": move,
        "why": why,
        "features": full,
        "breakdown": breakdown(full),
        "score": score(full),
    }


def candidate_moves(root: Path) -> list[dict[str, Any]]:
    """Build the full set of candidate moves from current state, each scored.

    Each candidate carries ONLY the features it actually activates; the rest stay
    zero. Scoring is applied uniformly by ``score`` — no per-move policy here.
    """
    root = root.resolve()
    moves: list[dict[str, Any]] = []
    energy = recent_energy(root)

    # --- Staleness: refresh the live STATE before deciding anything else. ----
    state_findings = _state_findings(root)
    stale = any(f.category == "state_stale_dates" for f in state_findings)
    missing_focus = any(f.category == "state_missing_focus" for f in state_findings)
    if stale or missing_focus:
        n = len([f for f in state_findings if f.category in {"state_stale_dates", "state_missing_focus"}])
        moves.append(
            _make_move(
                "Refresh the stale STATE.md (Current Focus) before deciding what to work on.",
                f"{n} STATE scope(s) stale or missing focus — the allocator can't aim well until the live state is current.",
                {
                    "staleness": min(1.0, 0.5 + 0.1 * n),
                    "energy_match": _energy_factor_for("light", energy),
                },
                kind="staleness",
            )
        )

    # --- Shippable-but-unfinished: finish/ship a captured thread going stale. -
    ship_items = ship_tracker.load_ship_items(root)
    captured = [i for i in ship_items if i.get("status") == "captured"]
    stale_captured = [i for i in captured if _ship_age_days(i) >= SHIP_STALE_DAYS]
    if stale_captured:
        # Push the oldest one specifically (closure beats breadth).
        oldest = max(stale_captured, key=_ship_age_days)
        age = int(_ship_age_days(oldest))
        income = " [priority]" if oldest.get("income") else ""
        moves.append(
            _make_move(
                f"Finish and ship \"{oldest.get('title')}\"{income} "
                f"(`exocortex-ship shape {oldest.get('id')}`).",
                f"Captured {age}d ago and stalling — closing it addresses the capture-without-closure problem"
                + (" and it is flagged higher-priority." if oldest.get("income") else "."),
                {
                    "shippable_unfinished": min(1.0, 0.4 + 0.05 * len(stale_captured)),
                    "energy_match": _energy_factor_for("light", energy),
                },
                kind="ship",
            )
        )

    # --- Surfaced items: things already flagged for attention. ---------------
    surfaced = _surfaced_count(root)
    if surfaced:
        moves.append(
            _make_move(
                f"Review the {surfaced} item(s) surfaced for your attention and decide.",
                f"{surfaced} item(s) are already surfaced — clearing them is fast and unblocks the loop.",
                {
                    "surfaced_priority": min(1.5, 0.5 + 0.1 * surfaced),
                    "energy_match": _energy_factor_for("light", energy),
                },
                kind="surfaced",
            )
        )

    # --- Queue pressure: review backlog + raw inbox over their thresholds. ----
    pending = _pending_count(root)
    raw = _raw_inbox_count(root)
    limit = context_hygiene.DEFAULT_PENDING_LIMIT
    if pending > limit:
        moves.append(
            _make_move(
                f"Triage the candidate backlog ({pending} pending) — `exocortex-review triage`.",
                f"{pending} pending candidates, over the {limit} limit — a triage pass keeps the loop closing.",
                {
                    "queue_pressure": min(2.0, pending / max(limit, 1)),
                    "energy_match": _energy_factor_for("heavy", energy),
                },
                kind="queue",
            )
        )
    elif pending:
        moves.append(
            _make_move(
                f"Clear the {pending} pending candidate(s) — one quick `exocortex-review` pass.",
                f"{pending} pending candidate(s) — small enough to clear in one pass.",
                {
                    "queue_pressure": min(1.0, pending / max(limit, 1)),
                    "energy_match": _energy_factor_for("light", energy),
                },
                kind="queue",
            )
        )
    if raw:
        moves.append(
            _make_move(
                f"Triage the raw inbox ({raw} file(s)) — `exocortex-ingest`.",
                f"{raw} raw file(s) waiting to be ingested into the wiki.",
                {
                    "queue_pressure": min(1.0, 0.2 + 0.02 * raw),
                    "energy_match": _energy_factor_for("light", energy),
                },
                kind="raw",
            )
        )

    # --- Fallback: nothing pressing -> route to the highest-priority thread. --
    if not moves:
        moves.append(
            _make_move(
                "Loop is clean — pick the most important thread right now and go deep.",
                "Nothing is stale, stalling, or queued. Spend the time on real work.",
                {"energy_match": _energy_factor_for("heavy", energy)},
                kind="freeform",
            )
        )

    moves.sort(key=lambda m: m["score"], reverse=True)
    return moves


# ===========================================================================
# THE PUBLIC API
# ===========================================================================


def _log_allocations(root: Path, moves: list[dict[str, Any]]) -> Path:
    """Append one training record per suggestion to ``journal/allocations.jsonl``.

    Flat, stable schema. ``taken`` and ``reward`` are left blank (null) for now —
    a later join fills them from ``reward-log.jsonl`` / ``review-decisions.jsonl``
    via the shared ``id``. This is the substrate a future learned policy trains
    on.
    """
    path = root / ALLOCATIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()
    with path.open("a", encoding="utf-8") as handle:
        for move in moves:
            entry = {
                "timestamp": stamp,
                "id": move["id"],
                "kind": move["kind"],
                "move": move["move"],
                "why": move["why"],
                "features": move["features"],
                "breakdown": move["breakdown"],
                "score": move["score"],
                # Blank reward slots, filled later by a join on `id`.
                "taken": None,
                "reward": None,
            }
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return path


def propose(
    root: Path,
    *,
    top: int = 3,
    log: bool = False,
    policy: Callable[[dict[str, float]], float] | None = None,
) -> list[dict[str, Any]]:
    """Return the top 1-3 next moves, highest score first.

    ``policy`` lets a caller (or a future learned/federated policy) override the
    scoring seam without touching the gatherers. When supplied, candidates are
    re-scored and re-ranked through it; the default is ``score`` (the v1 weights).

    When ``log`` is True, every returned suggestion is appended to
    ``journal/allocations.jsonl`` as training data.
    """
    root = root.resolve()
    moves = candidate_moves(root)

    if policy is not None:
        for move in moves:
            move["score"] = round(policy(move["features"]), 4)
        moves.sort(key=lambda m: m["score"], reverse=True)

    top = max(1, min(top, 3))
    moves = moves[:top]

    if log:
        _log_allocations(root, moves)
    return moves


# ===========================================================================
# CLI  (`exocortex-next`)
# ===========================================================================


def render(moves: list[dict[str, Any]], *, why: bool) -> str:
    lines: list[str] = []
    for index, move in enumerate(moves, start=1):
        lines.append(f"{index}. {move['move']}")
        lines.append(f"   why: {move['why']}")
        if why:
            lines.append(f"   score: {move['score']}")
            # Show the full declared feature space (zeros included) so the
            # weighted-score policy is fully inspectable.
            parts = [f"{name}={move['breakdown'][name]}" for name in FEATURE_SCHEMA]
            lines.append("   breakdown: " + ", ".join(parts))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "The ExoCortex Allocator — 'what now': the top 1-3 next best moves, "
            "each with a one-line why and a transparent score breakdown."
        )
    )
    parser.add_argument("--root", default=None, help="ExoCortex root.")
    parser.add_argument(
        "--why",
        action="store_true",
        help="Show the per-feature score breakdown for each move.",
    )
    parser.add_argument(
        "--top", type=int, default=3, help="How many moves to show (1-3)."
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Do not append suggestions to journal/allocations.jsonl.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the moves as JSON."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)
    moves = propose(root, top=args.top, log=not args.no_log)
    if args.json:
        print(json.dumps(moves, indent=2, sort_keys=True))
        return 0
    if not moves:
        print("No moves proposed.")
        return 0
    print(render(moves, why=args.why))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
