#!/usr/bin/env python3
"""The Ship tracker — the output arm, v1.

This is the closure surface for capturable threads: a small record of things
worth finishing, each moving through a clear lifecycle
``captured -> shaped -> shipped``. It addresses the capture-without-closure
problem — capture works, closure does not.

Data shape (append-or-update JSONL at ``journal/ship.jsonl``). One record per
item; the file is rewritten on update (latest-wins), but every durable change is
also recorded to the Logbook and is reversible. Each item:

- ``id``       — short stable id (slug of title + counter; unique).
- ``title``    — human label.
- ``status``   — one of ``captured`` / ``shaped`` / ``shipped``.
- ``channel``  — one of ``essay`` / ``post`` / ``code`` / ``oss`` / ``product`` /
                 ``other``.
- ``income``   — bool; flags an item as higher-priority.
- ``created``  — ISO timestamp, set once.
- ``updated``  — ISO timestamp, bumped on every change.
- ``link``     — optional pointer (seed path, URL, repo); set by ``shape`` or add.

The read API the Allocator calls is ``load_ship_items(root)``. The Allocator
reads this so "what now" can propose *finishing and shipping* something, not only
starting new work.

The shape hook: ``shape <id>`` prepares (or links) a draft outline in the seeds
folder and emits a ready-to-paste prompt, then flips the item to ``shaped``. It
does not run an LLM; this just sets the stage and prints the prompt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root

from tools.workers import logbook

SHIP_PATH = Path("journal/ship.jsonl")
SEEDS_DIR = Path("domains/writing/projects/essays/seeds")

STATUSES = ("captured", "shaped", "shipped")
CHANNELS = ("essay", "post", "code", "oss", "product", "other")
# Linear lifecycle: advancing moves to the next status; shipped is terminal.
NEXT_STATUS = {"captured": "shaped", "shaped": "shipped", "shipped": "shipped"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- Persistence -----------------------------------------------------------


def load_ship_items(root: Path) -> list[dict[str, Any]]:
    """Read all Ship-tracker items, oldest first.

    This is the read API the Allocator calls. Returns a list of plain dicts with
    the stable schema documented at module top. Never raises on a missing file
    (returns ``[]``); skips any unparseable line rather than failing the read.
    """
    path = root / SHIP_PATH
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _write_items(root: Path, items: list[dict[str, Any]]) -> None:
    path = root / SHIP_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, sort_keys=True) + "\n")


def get_item(root: Path, item_id: str) -> dict[str, Any]:
    for item in load_ship_items(root):
        if item.get("id") == item_id:
            return item
    raise KeyError(f"No ship item with id: {item_id}")


# --- Ids -------------------------------------------------------------------


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "item"


def _make_id(root: Path, title: str) -> str:
    """Unique short id: title slug, suffixed with a counter only on collision."""
    base = _slugify(title)[:48]
    existing = {item.get("id") for item in load_ship_items(root)}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


# --- Mutations (each records to the Logbook, reversible) --------------------


def add_item(
    root: Path,
    *,
    title: str,
    channel: str = "other",
    income: bool = False,
    link: str | None = None,
) -> dict[str, Any]:
    """Add a captured thread worth finishing. Records to the Logbook."""
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    title = " ".join(title.split()).strip()
    if not title:
        raise ValueError("title is required")

    items = load_ship_items(root)
    stamp = now_iso()
    item = {
        "id": _make_id(root, title),
        "title": title,
        "status": "captured",
        "channel": channel,
        "income": bool(income),
        "created": stamp,
        "updated": stamp,
        "link": link,
    }
    items.append(item)
    _write_items(root, items)

    logbook.record_change(
        root,
        actor="ship.add",
        action="ship-add",
        path=SHIP_PATH,
        summary=f"captured ship item: {title}",
        reversal={"item_id": item["id"], "prior": None},
        metadata={"channel": channel, "income": bool(income)},
    )
    return item


def _apply_status(
    root: Path, item_id: str, new_status: str, *, actor: str, action: str
) -> dict[str, Any]:
    items = load_ship_items(root)
    target: dict[str, Any] | None = None
    for item in items:
        if item.get("id") == item_id:
            target = item
            break
    if target is None:
        raise KeyError(f"No ship item with id: {item_id}")

    prior_status = target.get("status")
    target["status"] = new_status
    target["updated"] = now_iso()
    _write_items(root, items)

    # No-op flips still get recorded so the audit trail is complete, but mark them.
    logbook.record_change(
        root,
        actor=actor,
        action=action,
        path=SHIP_PATH,
        summary=f"ship {item_id}: {prior_status} -> {new_status}: {target.get('title')}",
        reversal={"item_id": item_id, "prior_status": prior_status},
        metadata={"title": target.get("title"), "channel": target.get("channel")},
    )
    return target


def set_status(root: Path, item_id: str, status: str) -> dict[str, Any]:
    """Set an item's status directly. Records a reversible Logbook entry."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    return _apply_status(root, item_id, status, actor="ship.set", action="ship-status-flip")


def advance(root: Path, item_id: str) -> dict[str, Any]:
    """Advance one step along the lifecycle (captured->shaped->shipped)."""
    current = get_item(root, item_id)
    new_status = NEXT_STATUS[current["status"]]
    return _apply_status(
        root, item_id, new_status, actor="ship.advance", action="ship-advance"
    )


# --- The shape hook --------------------------------------------------------


SEED_TEMPLATE = """# {title}

status: shaped (ship item `{item_id}`)
channel: {channel}
created: {date}

## Summary


## Why it matters / why it could ship


## Main point


## Open questions / what to sharpen

"""


def _seed_path_for(root: Path, item: dict[str, Any]) -> Path:
    return root / SEEDS_DIR / f"{_slugify(item['title'])}.md"


def shape(
    root: Path, item_id: str, *, seed: str | None = None
) -> dict[str, Any]:
    """Kick off the shaping path for a captured thread (the one-step "shape this").

    Creates a draft outline (or links an existing one given via ``seed``), links
    it on the item, flips the item to ``shaped``, and returns a ready-to-paste
    prompt. This does NOT run an LLM; "kick off" here means prepare the outline
    and print the prompt to run it.

    Returns ``{"item", "seed", "prompt"}``.
    """
    item = get_item(root, item_id)

    if seed is not None:
        seed_rel = seed
        seed_path = root / seed_rel
        if not seed_path.exists():
            raise FileNotFoundError(f"seed not found: {seed_rel}")
    else:
        seed_path = _seed_path_for(root, item)
        seed_rel = str(seed_path.relative_to(root))
        if not seed_path.exists():
            seed_path.parent.mkdir(parents=True, exist_ok=True)
            seed_path.write_text(
                SEED_TEMPLATE.format(
                    title=item["title"],
                    item_id=item["id"],
                    channel=item.get("channel", "other"),
                    date=now_iso()[:10],
                ),
                encoding="utf-8",
            )
            logbook.record_change(
                root,
                actor="ship.shape",
                action="seed-create",
                path=seed_path,
                summary=f"created shaping seed for ship item {item_id}: {item['title']}",
                reversal={"prior_content": None, "file_created": True},
                metadata={"item_id": item_id},
            )

    # Link the seed and flip to shaped (records its own Logbook entry).
    items = load_ship_items(root)
    for it in items:
        if it.get("id") == item_id:
            it["link"] = seed_rel
            break
    _write_items(root, items)
    updated = _apply_status(
        root, item_id, "shaped", actor="ship.shape", action="ship-shape"
    )

    prompt = (
        f"Shape this into a publishable {item.get('channel', 'piece')}. "
        f"The draft outline is at `{seed_rel}` (ship item `{item_id}`: "
        f"\"{item['title']}\"). "
        "Gather the material, connect and expand it, sharpen and cross-check it, "
        "then draft it in the chosen form. Cut it if it's weak. Hand back what's "
        "strong, weak, and unresolved in plain language."
    )
    return {"item": updated, "seed": seed_rel, "prompt": prompt}


# --- CLI -------------------------------------------------------------------


def _fmt_item(item: dict[str, Any]) -> str:
    income = " [priority]" if item.get("income") else ""
    link = f"  -> {item['link']}" if item.get("link") else ""
    return f"  {item['id']} | {item['channel']}{income} | {item['title']}{link}"


def cmd_list(root: Path) -> int:
    items = load_ship_items(root)
    if not items:
        print("No ship items. Add one with: exocortex-ship add \"<title>\"")
        return 0
    for status in STATUSES:
        group = [i for i in items if i.get("status") == status]
        if not group:
            continue
        print(f"{status} ({len(group)}):")
        for item in group:
            print(_fmt_item(item))
    return 0


def cmd_add(root: Path, title: str, channel: str, income: bool, link: str | None) -> int:
    item = add_item(root, title=title, channel=channel, income=income, link=link)
    print(f"captured: {item['id']} | {item['title']} ({item['channel']})")
    return 0


def cmd_advance(root: Path, item_id: str) -> int:
    try:
        item = advance(root, item_id)
    except KeyError as exc:
        print(str(exc))
        return 1
    print(f"{item['id']}: now {item['status']}")
    return 0


def cmd_set(root: Path, item_id: str, status: str) -> int:
    try:
        item = set_status(root, item_id, status)
    except KeyError as exc:
        print(str(exc))
        return 1
    print(f"{item['id']}: now {item['status']}")
    return 0


def cmd_shape(root: Path, item_id: str, seed: str | None) -> int:
    try:
        result = shape(root, item_id, seed=seed)
    except KeyError as exc:
        print(str(exc))
        return 1
    print(f"shaped: {result['item']['id']} -> seed `{result['seed']}`")
    print("")
    print("Paste this to draft it:")
    print("")
    print(result["prompt"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="The ExoCortex Ship tracker (output arm)."
    )
    parser.add_argument("--root", default=None, help="ExoCortex root.")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Capture a thread worth finishing.")
    add.add_argument("title")
    add.add_argument("--channel", default="other", choices=CHANNELS)
    add.add_argument("--income", action="store_true", help="Flag as higher-priority.")
    add.add_argument("--link", default=None, help="Optional pointer (seed path, URL, repo).")

    advance_p = sub.add_parser("advance", help="Advance one step (captured->shaped->shipped).")
    advance_p.add_argument("id")

    set_p = sub.add_parser("set", help="Set status directly.")
    set_p.add_argument("id")
    set_p.add_argument("status", choices=STATUSES)

    sub.add_parser("list", help="List items grouped by status.")

    shape_p = sub.add_parser(
        "shape", help="Prepare/link a draft outline and emit the prompt; flip to shaped."
    )
    shape_p.add_argument("id")
    shape_p.add_argument("--seed", default=None, help="Link an existing seed instead of creating one.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)
    if args.command == "add":
        return cmd_add(root, args.title, args.channel, args.income, args.link)
    if args.command == "advance":
        return cmd_advance(root, args.id)
    if args.command == "set":
        return cmd_set(root, args.id, args.status)
    if args.command == "list":
        return cmd_list(root)
    if args.command == "shape":
        return cmd_shape(root, args.id, args.seed)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
