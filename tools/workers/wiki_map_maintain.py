#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root
IGNORED_PARTS = {".git", "_exports", "_external", "__pycache__", "node_modules", ".venv", "venv"}


@dataclass
class WikiEntry:
    topic: str
    path: str
    summary: str
    status: str


def ignored(path: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.parts)


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    try:
        _, body, _rest = text.split("---", 2)
    except ValueError:
        return {}
    data: dict[str, str] = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def first_heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def clean_topic(text: str) -> str:
    text = re.sub(r"\bwiki\b", "", text, flags=re.I)
    text = re.sub(r"\bindex\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text or "Untitled wiki"


def topic_for_index(root: Path, index_path: Path, frontmatter: dict[str, str], text: str) -> str:
    rel_parts = index_path.relative_to(root).parts
    if rel_parts == ("wiki", "index.md"):
        return "ExoCortex system"
    heading = first_heading(text)
    if heading:
        return clean_topic(heading)
    title = frontmatter.get("title")
    if title:
        return clean_topic(title)
    if "projects" in rel_parts:
        project_index = rel_parts.index("projects") + 1
        if project_index < len(rel_parts):
            return rel_parts[project_index].replace("-", " ")
    if len(rel_parts) >= 3 and rel_parts[0] == "domains":
        return rel_parts[1].replace("-", " ")
    return clean_topic(index_path.parent.name)


def summary_for_index(frontmatter: dict[str, str], text: str) -> str:
    summary = frontmatter.get("summary", "").strip()
    if summary:
        return summary
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("---") or ":" in stripped:
            continue
        return stripped[:180]
    return "Managed wiki."


def discover_wikis(root: Path) -> list[WikiEntry]:
    entries: list[WikiEntry] = []
    for index_path in sorted(root.rglob("wiki/index.md")):
        if ignored(index_path.relative_to(root)):
            continue
        text = index_path.read_text(encoding="utf-8", errors="ignore")
        frontmatter = parse_frontmatter(text)
        wiki_path = index_path.parent.relative_to(root)
        entries.append(
            WikiEntry(
                topic=topic_for_index(root, index_path, frontmatter, text),
                path=f"{wiki_path}/",
                summary=summary_for_index(frontmatter, text),
                status=frontmatter.get("status", "active") or "active",
            )
        )
    root_index = root / "wiki" / "index.md"
    if root_index.exists() and not any(entry.path == "wiki/" for entry in entries):
        text = root_index.read_text(encoding="utf-8", errors="ignore")
        frontmatter = parse_frontmatter(text)
        entries.insert(
            0,
            WikiEntry(
                topic="ExoCortex system",
                path="wiki/",
                summary=summary_for_index(frontmatter, text),
                status=frontmatter.get("status", "active") or "active",
            ),
        )
    return entries


def render_wiki_map(entries: list[WikiEntry]) -> str:
    lines = [
        "# Wiki Map",
        "",
        "Compact topic directory across all wikis. Updated by knowledge-steward after any wiki write.",
        "",
        "---",
        "",
    ]
    for entry in sorted(entries, key=lambda item: item.topic.lower()):
        lines.extend(
            [
                f"## topic: {entry.topic}",
                "wikis:",
                f"  - path: {entry.path}",
                f"    summary: {entry.summary}",
                f"    status: {entry.status}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def refresh(root: Path, apply: bool) -> str:
    content = render_wiki_map(discover_wikis(root))
    if apply:
        (root / "wiki-map.md").write_text(content, encoding="utf-8")
    return content


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh the ExoCortex wiki-map.")
    parser.add_argument("--root", default=None)
    parser.add_argument("--apply", action="store_true", help="Write wiki-map.md.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)
    content = refresh(root, args.apply)
    if args.apply:
        print("updated wiki-map.md")
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
