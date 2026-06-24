#!/usr/bin/env python3
"""The feedback log + session-close check-in.

Every session can produce one labeled record so a future scoring policy has
training data from day one. The check-in is deliberately tiny: **one number
(rating 1-5) + one short line**. Both are skippable; the prompt never blocks,
hangs, or nags.

Three surfaces:

- **Feedback log** — append-only JSONL at ``journal/reward-log.jsonl``. One
  record per session. Designed to join with ``review-decisions.jsonl`` later
  (same id style: ``session_id`` is the local artifact id, ``claude_session_id``
  the harness id) so review decisions and feedback line up per session.

- **Pending check-ins** — append-only JSONL at
  ``journal/inbox/pending-checkins.jsonl``. Sessions that cannot prompt (Stop
  hook, desktop app, non-tty) record a *pending* entry here; the Brief surfaces
  the count and ``exocortex-checkin`` answers them after the fact.

- **The check-in** — ``run_checkin`` fires at the close of an interactive
  wrapper session. Enter = skip (still logs a row, fields null); non-tty
  auto-defers to a pending entry. A short input timeout guards against a stream
  that looks like a tty but never delivers a line.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root

REWARD_LOG_PATH = Path("journal/reward-log.jsonl")
PENDING_CHECKINS_PATH = Path("journal/inbox/pending-checkins.jsonl")

# Seconds to wait for a line at an interactive prompt before giving up and
# treating it as a skip. Keeps the check-in from ever stalling a terminal that
# claims to be a tty but never delivers input (detached shells, odd CI ttys).
CHECKIN_INPUT_TIMEOUT = 30.0


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- Feedback log ----------------------------------------------------------


def parse_energy(raw: str | None) -> int | None:
    """Coerce a raw rating answer to an int in [1, 5], else ``None``.

    Anything that is not a clean 1-5 (blank, "skip", out of range, garbage) is
    treated as "no answer" — the check-in is allowed to be skipped freely.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    if 1 <= value <= 5:
        return value
    return None


def parse_juice(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = " ".join(raw.split()).strip()
    return text or None


def append_reward(
    root: Path,
    *,
    session_id: str,
    claude_session_id: str | None,
    agent: str | None,
    scope: str | None,
    energy: int | None,
    juice: str | None,
    source: str,
) -> Path:
    """Append one feedback record. Flat, stable schema so the log loads directly
    as a training dataset and joins with ``review-decisions.jsonl`` by id."""
    entry = {
        "timestamp": now_iso(),
        "session_id": session_id,
        "claude_session_id": claude_session_id,
        "agent": agent,
        "scope": scope,
        "energy": energy,
        "juice": juice,
        "source": source,
    }
    path = root / REWARD_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return path


# --- Pending check-ins -----------------------------------------------------


def record_pending(
    root: Path,
    *,
    session_id: str,
    claude_session_id: str | None,
    agent: str | None,
    scope: str | None,
    source: str,
) -> Path:
    """Record a session that could not be rated interactively, so it can be
    rated later (via the Brief prompt -> ``exocortex-checkin``)."""
    entry = {
        "recorded_at": now_iso(),
        "session_id": session_id,
        "claude_session_id": claude_session_id,
        "agent": agent,
        "scope": scope,
        "source": source,
    }
    path = root / PENDING_CHECKINS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return path


def load_pending(root: Path) -> list[dict[str, Any]]:
    path = root / PENDING_CHECKINS_PATH
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


def pending_count(root: Path) -> int:
    return len(load_pending(root))


def _rewrite_pending(root: Path, items: list[dict[str, Any]]) -> None:
    path = root / PENDING_CHECKINS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not items:
        # Truncate rather than delete so the surface keeps existing (empty).
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, sort_keys=True) + "\n")


def answer_pending(
    root: Path,
    session_id: str,
    *,
    energy: int | None,
    juice: str | None,
) -> bool:
    """Answer one pending check-in: write its feedback row and remove it from the
    pending queue. Returns ``True`` if a pending entry matched.

    The pending entry carries the original session metadata so the record stays
    joinable to that session even though it is being rated after the fact.
    """
    pending = load_pending(root)
    matched: dict[str, Any] | None = None
    remaining: list[dict[str, Any]] = []
    for item in pending:
        if matched is None and item.get("session_id") == session_id:
            matched = item
            continue
        remaining.append(item)
    if matched is None:
        return False
    append_reward(
        root,
        session_id=matched.get("session_id", session_id),
        claude_session_id=matched.get("claude_session_id"),
        agent=matched.get("agent"),
        scope=matched.get("scope"),
        energy=energy,
        juice=juice,
        source=matched.get("source", "checkin-deferred"),
    )
    _rewrite_pending(root, remaining)
    return True


# --- The interactive check-in ----------------------------------------------

ENERGY_PROMPT = "[exo] session close — rate this session 1-5? (Enter to skip) "
JUICE_PROMPT = "[exo] what stood out? (one line; Enter to skip) "


def _read_line_with_timeout(
    stdin: TextIO, timeout: float, is_real_tty: bool
) -> str | None:
    """Read one line, giving up after ``timeout`` seconds.

    Returns the line (without trailing newline), or ``None`` on timeout / EOF.
    Only a real terminal fd is polled with ``select``; injected stream objects
    (tests, pipes) are read directly so they stay deterministic and never hang
    on a fd that isn't selectable.
    """
    if is_real_tty:
        import select

        try:
            ready, _, _ = select.select([stdin], [], [], timeout)
        except (ValueError, OSError):
            ready = [stdin]
        if not ready:
            return None
    line = stdin.readline()
    if line == "":
        return None
    return line.rstrip("\n")


def _open_controlling_tty() -> TextIO | None:
    """A fresh read handle on the controlling terminal, or ``None``.

    The check-in runs *after* the postprocess worker, whose summarizer
    subprocesses inherit the wrapper's stdin (fd 0) and read from it — leaving
    fd 0 drained or at EOF by the time we prompt. Opening ``/dev/tty`` gives an
    independent handle to the real terminal that those subprocesses cannot have
    disturbed, so the prompt waits for the user instead of skipping instantly.
    """
    try:
        return open("/dev/tty", "r", encoding="utf-8", errors="replace")
    except OSError:
        return None


def run_checkin(
    root: Path,
    *,
    session_id: str,
    claude_session_id: str | None,
    agent: str | None,
    scope: str | None,
    stdin: TextIO | None = None,
    output: TextIO | None = None,
    is_tty: bool | None = None,
    timeout: float = CHECKIN_INPUT_TIMEOUT,
) -> str:
    """Run the session-close check-in.

    Returns one of:
      - ``"deferred"`` — non-tty; recorded a pending entry, no prompt shown.
      - ``"skipped"``  — prompted, both answers blank/timeout; logged a null row.
      - ``"answered"`` — prompted, at least one answer given; logged the row.

    Never raises, never blocks indefinitely, never nags. A single Enter clears
    each prompt; a non-tty session is auto-deferred without any keystroke.
    """
    output = output if output is not None else sys.stderr

    # When the caller injects a stream (tests, pipes) honour it verbatim. With no
    # override, read the controlling terminal directly via /dev/tty rather than
    # the inherited fd 0, which the postprocess worker's summarizer subprocesses
    # may have drained or closed. Fall back to sys.stdin if /dev/tty is absent.
    tty_handle: TextIO | None = None
    if stdin is None:
        tty_handle = _open_controlling_tty()
        stdin = tty_handle if tty_handle is not None else sys.stdin

    if is_tty is None:
        try:
            is_tty = bool(stdin.isatty())
        except Exception:
            is_tty = False

    if not is_tty:
        if tty_handle is not None:
            tty_handle.close()
        record_pending(
            root,
            session_id=session_id,
            claude_session_id=claude_session_id,
            agent=agent,
            scope=scope,
            source="checkin-deferred",
        )
        return "deferred"

    # A genuine terminal fd can be polled; an injected StringIO (tests) cannot.
    is_real_tty = False
    try:
        is_real_tty = hasattr(stdin, "fileno") and stdin.isatty()
    except Exception:
        is_real_tty = False

    def ask(prompt: str) -> str | None:
        try:
            output.write(prompt)
            output.flush()
        except Exception:
            pass
        return _read_line_with_timeout(stdin, timeout, is_real_tty)

    try:
        energy = parse_energy(ask(ENERGY_PROMPT))
        juice = parse_juice(ask(JUICE_PROMPT))
    finally:
        if tty_handle is not None:
            tty_handle.close()

    append_reward(
        root,
        session_id=session_id,
        claude_session_id=claude_session_id,
        agent=agent,
        scope=scope,
        energy=energy,
        juice=juice,
        source="wrapper",
    )
    try:
        if energy is None and juice is None:
            output.write("[exo] skipped — noted.\n")
        else:
            output.write("[exo] logged. thanks.\n")
        output.flush()
    except Exception:
        pass
    return "answered" if (energy is not None or juice is not None) else "skipped"


# --- exocortex-checkin entrypoint ------------------------------------------


def _prompt_answers(
    stdin: TextIO, output: TextIO
) -> tuple[int | None, str | None]:
    is_real_tty = False
    try:
        is_real_tty = hasattr(stdin, "fileno") and stdin.isatty()
    except Exception:
        is_real_tty = False

    output.write(ENERGY_PROMPT)
    output.flush()
    energy = parse_energy(_read_line_with_timeout(stdin, CHECKIN_INPUT_TIMEOUT, is_real_tty))
    output.write(JUICE_PROMPT)
    output.flush()
    juice = parse_juice(_read_line_with_timeout(stdin, CHECKIN_INPUT_TIMEOUT, is_real_tty))
    return energy, juice


def cmd_pending(root: Path) -> int:
    pending = load_pending(root)
    if not pending:
        print("No pending check-ins. All sessions rated.")
        return 0
    print(f"{len(pending)} session(s) not yet rated:")
    for item in pending:
        when = item.get("recorded_at", "?")
        print(
            f"  {item.get('session_id', '?')} | {when} | "
            f"agent={item.get('agent', '?')} | scope={item.get('scope', '?')}"
        )
    return 0


def cmd_rate(
    root: Path,
    session_id: str | None,
    *,
    energy_arg: str | None = None,
    juice_arg: str | None = None,
    stdin: TextIO | None = None,
    output: TextIO | None = None,
) -> int:
    """Rate one pending session (or the oldest pending if none given). The
    ``juice`` argument is the free-text field captured alongside the rating."""
    stdin = stdin if stdin is not None else sys.stdin
    output = output if output is not None else sys.stdout

    pending = load_pending(root)
    if not pending:
        output.write("No pending check-ins to rate.\n")
        return 0

    target = session_id
    if target is None:
        target = pending[0].get("session_id")
        output.write(
            f"Rating oldest pending session: {target} "
            f"(agent={pending[0].get('agent', '?')})\n"
        )

    if energy_arg is not None or juice_arg is not None:
        energy = parse_energy(energy_arg)
        juice = parse_juice(juice_arg)
    else:
        energy, juice = _prompt_answers(stdin, output)

    if answer_pending(root, target, energy=energy, juice=juice):
        output.write(
            f"Logged feedback for {target} (rating={energy}, note={juice!r}).\n"
        )
        return 0
    output.write(f"No pending check-in matched session id: {target}\n")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Answer pending ExoCortex session check-ins "
            "(rating 1-5 + one short line)."
        )
    )
    parser.add_argument("--root", default=None, help="ExoCortex root.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("pending", help="List sessions not yet rated.")

    rate = sub.add_parser(
        "rate", help="Rate a pending session (oldest if no id given)."
    )
    rate.add_argument("session_id", nargs="?", default=None)
    rate.add_argument("--energy", default=None, help="Rating 1-5 (skip the prompt).")
    rate.add_argument("--juice", default=None, help="One short line (skip the prompt).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_instance_root(args.root)
    # Default (no subcommand) = rate the oldest pending session.
    if args.command in (None, "rate"):
        return cmd_rate(
            root,
            getattr(args, "session_id", None),
            energy_arg=getattr(args, "energy", None),
            juice_arg=getattr(args, "juice", None),
        )
    if args.command == "pending":
        return cmd_pending(root)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
