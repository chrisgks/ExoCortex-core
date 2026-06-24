#!/usr/bin/env python3

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def uncategorized_inbox_files(root: Path) -> list[str]:
    inbox = root / "raw" / "inbox"
    if not inbox.exists():
        return []
    files = []
    for path in sorted(inbox.iterdir()):
        if path.is_dir():
            continue
        if path.name == "README.md":
            continue
        files.append(str(path.relative_to(root)))
    return files


def bucket_count(root: Path, relative_dir: str) -> int:
    directory = root / relative_dir
    if not directory.exists():
        return 0
    return len([path for path in directory.rglob("*.md") if path.is_file()])


def pending_intents(root: Path, limit: int = 5) -> list[str]:
    queue = root / "journal" / "inbox" / "pending-intents.md"
    if not queue.exists():
        return []
    items: list[str] = []
    for line in queue.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            items.append(line.removeprefix("### ").strip())
    return items[:limit]


def build_status(root: Path) -> str:
    uncategorized = uncategorized_inbox_files(root)
    concepts = bucket_count(root, "raw/inbox/concept_seeds")
    designs = bucket_count(root, "raw/inbox/design_notes")
    intents = pending_intents(root)

    lines = [
        "# Automation Status",
        "",
        f"- generated_at: `{now_iso()}`",
        "",
        "## Raw Inbox",
        "",
        f"- uncategorized_files: `{len(uncategorized)}`",
        f"- concept_seed_files: `{concepts}`",
        f"- design_note_files: `{designs}`",
        "",
    ]
    if uncategorized:
        lines.extend(["### Uncategorized Files", ""])
        lines.extend(f"- `{item}`" for item in uncategorized)
        lines.append("")
        lines.extend(
            [
                "### Suggested Action",
                "",
                "- Run inbox triage on the uncategorized files before any wiki ingestion.",
                "",
            ]
        )
    else:
        lines.extend(["### Uncategorized Files", "", "- None.", ""])

    lines.extend(["## Pending Intents", ""])
    if intents:
        lines.extend(f"- {item}" for item in intents)
        lines.append("")
        lines.extend(
            [
                "### Suggested Action",
                "",
                "- Review whether any repeated inferred intent should be confirmed and promoted into `system/OPEN LOOPS.md`.",
                "",
            ]
        )
    else:
        lines.extend(["- None queued.", ""])

    return "\n".join(lines)


def main() -> int:
    output = ROOT / "journal" / "inbox" / "automation-status.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_status(ROOT), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
