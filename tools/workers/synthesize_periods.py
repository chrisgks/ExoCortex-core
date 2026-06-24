#!/usr/bin/env python3
"""Build / backfill axis-organized period syntheses (week, month, quarter).

Weekly rolls up that week's session intelligence records; monthly rolls up the
month's weekly syntheses; quarterly rolls up the quarter's monthly syntheses —
the same model path process_session.py runs at session-end, but on demand and
for arbitrary periods. Use it to backfill all of history:

    python3 tools/workers/synthesize_periods.py --level all --all-history --apply

Dry-run by default (prints the periods it would build). Build order is always
weeks -> months -> quarters so each level rolls up a freshly-built lower level.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workers.process_session import (
    _assemble_period_input,
    _source_periods_footer,
    load_month_weeklies,
    load_quarter_monthlies,
    load_weekly_intelligence,
    monthly_id,
    now_iso,
    quarterly_id,
    read_json,
    render_period_synthesis,
    render_weekly_synthesis,
    weekly_id,
)

LEVELS = ("week", "month", "quarter")


def all_intelligence(root: Path):
    for path in sorted((root / "journal" / "sessions").glob("*/*.intelligence.json")):
        try:
            yield read_json(path)
        except Exception:
            continue


def enumerate_periods(root: Path) -> dict[str, list[str]]:
    """Distinct, real (non-`unknown-*`) period ids present in the session data."""
    found: dict[str, set[str]] = {"week": set(), "month": set(), "quarter": set()}
    for rec in all_intelligence(root):
        started = rec.get("started_at", "")
        if not started:
            continue
        found["week"].add(weekly_id(started))
        found["month"].add(monthly_id(started))
        found["quarter"].add(quarterly_id(started))
    return {
        level: sorted(p for p in ids if not p.startswith("unknown"))
        for level, ids in found.items()
    }


def build_week(root: Path, week: str) -> bool:
    records = load_weekly_intelligence(root, week)
    if not records:
        return False
    render_weekly_synthesis(root, week, records)
    return True


def build_month(root: Path, month: str) -> bool:
    weeklies = load_month_weeklies(root, month)
    if not weeklies:
        return False
    anchor = max((w.get("anchor_date", "") for w in weeklies), default="") or now_iso()
    record = render_period_synthesis(
        root, "month", month, f"Month {month}", anchor,
        _assemble_period_input(weeklies),
        source_count=len(weeklies), footer_lines=_source_periods_footer(weeklies),
    )
    return record is not None


def build_quarter(root: Path, quarter: str) -> bool:
    monthlies = load_quarter_monthlies(root, quarter)
    if not monthlies:
        return False
    anchor = max((m.get("anchor_date", "") for m in monthlies), default="") or now_iso()
    record = render_period_synthesis(
        root, "quarter", quarter, f"Quarter {quarter}", anchor,
        _assemble_period_input(monthlies),
        source_count=len(monthlies), footer_lines=_source_periods_footer(monthlies),
    )
    return record is not None


BUILDERS = {"week": build_week, "month": build_month, "quarter": build_quarter}


def selected_levels(level: str) -> list[str]:
    return list(LEVELS) if level == "all" else [level]


def resolve_targets(root: Path, args: argparse.Namespace) -> dict[str, list[str]]:
    levels = selected_levels(args.level)
    targets: dict[str, list[str]] = {level: [] for level in LEVELS}
    if args.all_history:
        enumerated = enumerate_periods(root)
        for level in levels:
            targets[level] = enumerated[level]
    elif args.current:
        now = now_iso()
        ids = {"week": weekly_id(now), "month": monthly_id(now), "quarter": quarterly_id(now)}
        for level in levels:
            targets[level] = [ids[level]]
    elif args.period:
        for level in levels:
            targets[level] = [args.period]
    return targets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--level", choices=("week", "month", "quarter", "all"), default="all")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--period", help="A single period id (e.g. 2026-W21, 2026-06, 2026-Q2).")
    group.add_argument("--current", action="store_true", help="Build the current week/month/quarter.")
    group.add_argument("--all-history", action="store_true", help="Build every period present in the data.")
    parser.add_argument("--apply", action="store_true", help="Actually build (default: dry-run).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.period and args.level == "all":
        build_parser().error("--period requires a specific --level (week|month|quarter), not 'all'.")
    root = Path(args.root).resolve()
    targets = resolve_targets(root, args)

    if not args.apply:
        for level in LEVELS:
            for period in targets[level]:
                print(f"{level:8} {period}")
        total = sum(len(targets[level]) for level in LEVELS)
        print(f"would build: {total}")
        return 0

    built = failed = 0
    # Strict order: weeks -> months -> quarters (rollup dependency).
    for level in LEVELS:
        for period in targets[level]:
            ok = BUILDERS[level](root, period)
            if ok:
                built += 1
                print(f"built {level} {period}", flush=True)
            else:
                failed += 1
                print(f"skipped/failed {level} {period}", flush=True)
    print(f"built: {built}")
    print(f"skipped or failed: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
