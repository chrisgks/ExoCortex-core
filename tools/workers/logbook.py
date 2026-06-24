#!/usr/bin/env python3

"""The Logbook — append-only record of every durable write, reversible.

Every write that changes a durable file or moves a file is recorded here so it
can be inspected and undone. Append-only JSONL, one record per line, never
deleted.

Two APIs:

- ``record_change(actor, action, path, summary, reversal)`` — the general
  write-record. Use this for new call sites. ``reversal`` carries enough to undo
  the change (prior content, prior status, the appended text, the source path of
  a move, etc.).
- ``append_entry(...)`` — the original maintenance-style helper (command /
  authority / reason / paths). Kept for the existing maintenance call sites; it
  writes the same JSONL stream so the Logbook stays a single record.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


LOGBOOK_PATH = Path("journal/logbook.jsonl")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def rel(root: Path, path: Path | str) -> str:
    path = Path(path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _write(root: Path, entry: dict[str, object]) -> Path:
    logbook = root / LOGBOOK_PATH
    logbook.parent.mkdir(parents=True, exist_ok=True)
    with logbook.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return logbook


def record_change(
    root: Path,
    *,
    actor: str,
    action: str,
    path: Path | str,
    summary: str,
    reversal: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> Path:
    """Append one durable-change record to the Logbook.

    actor    -- which worker / decision made the change (e.g. "review.accept").
    action   -- what happened (e.g. "append", "move", "status-flip").
    path     -- the durable target that changed (repo-relative is fine).
    summary  -- a human-readable one-liner.
    reversal -- enough to undo it: prior content / prior status / the appended
                text / the source path of a move. Stored verbatim.
    """
    entry = {
        "timestamp": now_iso(),
        "actor": actor,
        "action": action,
        "path": rel(root, path),
        "summary": summary,
        "reversal": reversal or {},
        "metadata": metadata or {},
    }
    return _write(root, entry)


def append_entry(
    root: Path,
    *,
    command: str,
    authority: str,
    reason: str,
    paths: Iterable[Path | str],
    status: str = "applied",
    metadata: dict[str, object] | None = None,
) -> Path:
    """Maintenance-style record (command/authority/reason/paths).

    Retained for the existing maintenance call sites (ingest, hygiene). Writes to
    the same Logbook JSONL stream as ``record_change``.
    """
    entry = {
        "timestamp": now_iso(),
        "command": command,
        "authority": authority,
        "reason": reason,
        "paths": [rel(root, path) for path in paths],
        "status": status,
        "metadata": metadata or {},
    }
    return _write(root, entry)
