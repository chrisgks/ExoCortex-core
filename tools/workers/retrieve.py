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
}


@dataclass
class SearchHit:
    path: str
    score: int
    excerpt: str


def ignored(path: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.parts)


def query_terms(query: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}", query)]


def candidate_files(root: Path, include_journal: bool = False) -> list[Path]:
    files = []
    for path in root.rglob("*.md"):
        rel = path.relative_to(root)
        if ignored(rel):
            continue
        if not include_journal and rel.parts and rel.parts[0] == "journal":
            continue
        files.append(path)
    return files


def best_excerpt(text: str, terms: list[str], max_chars: int = 240) -> str:
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n\s*\n", text)]
    scored: list[tuple[int, str]] = []
    for paragraph in paragraphs:
        lowered = paragraph.lower()
        score = sum(lowered.count(term) for term in terms)
        if score:
            scored.append((score, paragraph))
    if not scored:
        return ""
    excerpt = max(scored, key=lambda item: (item[0], len(item[1])))[1]
    return excerpt[:max_chars].rstrip()


def search(root: Path, query: str, limit: int = 8, include_journal: bool = False) -> list[SearchHit]:
    terms = query_terms(query)
    if not terms:
        return []
    hits: list[SearchHit] = []
    for path in candidate_files(root, include_journal=include_journal):
        rel_path = path.relative_to(root)
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        normalized_query = query.lower()
        path_score = sum(str(rel_path).lower().count(term) for term in terms) * 3
        score = path_score + sum(lowered.count(term) for term in terms)
        if normalized_query in str(rel_path).lower() or normalized_query in lowered[:300]:
            score += 50
        if not score:
            continue
        hits.append(
            SearchHit(
                path=str(rel_path),
                score=score,
                excerpt=best_excerpt(text, terms),
            )
        )
    hits.sort(key=lambda hit: (-hit.score, hit.path))
    return hits[:limit]


def render_hits(hits: list[SearchHit]) -> str:
    if not hits:
        return "No matches.\n"
    lines: list[str] = []
    for hit in hits:
        lines.extend(
            [
                f"## {hit.path}",
                "",
                f"- score: {hit.score}",
                f"- excerpt: {hit.excerpt}",
                "",
            ]
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search ExoCortex markdown beyond active preload context.")
    parser.add_argument("query")
    parser.add_argument("--root", default=None)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--include-journal", action="store_true", help="Include journal files and transcripts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)
    sys.stdout.write(render_hits(search(root, args.query, args.limit, include_journal=args.include_journal)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
