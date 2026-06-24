#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root

from tools.workers import wiki_map_maintain
from tools.workers import logbook


IGNORED_NAMES = {"README.md", ".DS_Store"}


@dataclass
class RawItem:
    path: Path
    target_wiki: Path
    source_note: Path


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def title_from_path(path: Path) -> str:
    title = path.stem.replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", title).title()


def slug_title(path: Path) -> str:
    return re.sub(r"\s+", " ", path.stem.replace("_", " ").strip())


def source_filename(path: Path) -> str:
    title = slug_title(path)
    return f"Source - {today()} - {title}.md"


def target_wiki_for_raw(root: Path, path: Path) -> Path:
    rel = path.relative_to(root)
    rel_text = str(rel)
    if "agentic-engineering" in rel_text:
        return root / "domains" / "learning" / "projects" / "agentic-engineering" / "wiki"
    return root / "wiki"


def discover_raw_items(root: Path, limit: int | None = None) -> list[RawItem]:
    raw_root = root / "raw" / "inbox"
    if not raw_root.exists():
        return []
    items: list[RawItem] = []
    for path in sorted(raw_root.rglob("*")):
        if not path.is_file() or path.name in IGNORED_NAMES:
            continue
        target_wiki = target_wiki_for_raw(root, path)
        source_note = target_wiki / "05_sources" / source_filename(path)
        items.append(RawItem(path=path, target_wiki=target_wiki, source_note=source_note))
        if limit is not None and len(items) >= limit:
            break
    return items


def summarize_raw(path: Path, max_chars: int = 500) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "Raw source file with no extracted text."
    summary = " ".join(lines[:4])
    summary = re.sub(r"\s+", " ", summary)
    return summary[:max_chars]


def processed_destination(root: Path, path: Path) -> Path:
    raw_root = root / "raw" / "inbox"
    rel = path.relative_to(raw_root)
    return root / "raw" / "processed" / today() / rel


def render_source_note(root: Path, item: RawItem, processed_path: Path) -> str:
    title = title_from_path(item.path)
    summary = summarize_raw(item.path)
    raw_rel = processed_path.relative_to(root)
    date = today()
    return "\n".join(
        [
            "---",
            f"title: {title}",
            "type: source",
            "status: seed",
            f"created: {date}",
            f"updated: {date}",
            f"summary: {summary}",
            "source_count: 1",
            "tags: raw-ingest",
            "---",
            "",
            f"# {title}",
            "",
            "## Summary",
            "",
            summary,
            "",
            "## Raw Source",
            "",
            f"- `{raw_rel}`",
            "",
            "## Open Questions",
            "",
            "- Needs knowledge-steward synthesis before promotion into concept or analysis pages.",
            "",
        ]
    )


def ensure_index_link(wiki: Path, source_note: Path) -> None:
    index = wiki / "index.md"
    if not index.exists():
        return
    text = index.read_text(encoding="utf-8")
    page = source_note.stem
    link = f"- [[{page}]] - Raw source note staged for synthesis. ({today()})"
    if f"[[{page}]]" in text:
        return
    if "## Sources" in text:
        text = text.replace("## Sources", f"## Sources\n\n{link}\n", 1)
    else:
        text = text.rstrip() + f"\n\n## Sources\n\n{link}\n"
    index.write_text(text, encoding="utf-8")


def append_wiki_log(wiki: Path, source_note: Path) -> None:
    log = wiki / "log.md"
    if not log.exists():
        return
    block = "\n".join(
        [
            f"## [{today()}] ingest | {source_note.stem}",
            "",
            "- Details: Created a seed source note from raw inbox and queued it for knowledge-steward synthesis.",
            "",
        ]
    )
    with log.open("a", encoding="utf-8") as handle:
        handle.write(block)


def move_to_processed(root: Path, path: Path) -> Path:
    dest = processed_destination(root, path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return dest


def ingest_item(root: Path, item: RawItem) -> Path:
    item.source_note.parent.mkdir(parents=True, exist_ok=True)
    processed_path = processed_destination(root, item.path)
    if not item.source_note.exists():
        item.source_note.write_text(render_source_note(root, item, processed_path), encoding="utf-8")
    ensure_index_link(item.target_wiki, item.source_note)
    append_wiki_log(item.target_wiki, item.source_note)
    move_to_processed(root, item.path)
    return item.source_note


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest raw inbox files into seed wiki source notes.")
    parser.add_argument("--root", default=None)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)
    items = discover_raw_items(root, args.limit)
    if not args.apply:
        for item in items:
            print(f"{item.path.relative_to(root)} -> {item.source_note.relative_to(root)}")
        print(f"ingestable: {len(items)}")
        return 1 if items else 0
    changed_paths: list[Path] = []
    for item in items:
        changed_paths.extend([item.source_note, processed_destination(root, item.path)])
        note = ingest_item(root, item)
        print(f"ingested: {note.relative_to(root)}")
    if items:
        wiki_map_maintain.refresh(root, apply=True)
        changed_paths.append(root / "wiki-map.md")
        logbook.append_entry(
            root,
            command="exocortex-ingest --apply",
            authority="safe_apply",
            reason="raw inbox ingestion approved",
            paths=changed_paths,
            metadata={"count": len(items)},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
