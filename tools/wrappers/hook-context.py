#!/usr/bin/env python3
"""SessionStart hook: emit the ExoCortex context manifest into Claude Code sessions
started outside the terminal wrapper (desktop app, web UI, sub-agents).

Two things are printed to stdout:

1. The full ExoCortex context manifest (model-facing). Harmless if the terminal
   wrapper also injects it via --append-system-prompt.
2. A short, human-readable Brief digest (what changed / what needs attention /
   the top next moves), printed LAST so it is the final thing on screen at
   session start. Rendered from `journal/inbox/brief.md`, the single read surface.
"""

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BRIEF_FILE = "journal/inbox/brief.md"


def _parse_brief_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+(.*)", line)
        if heading:
            current = heading.group(1).strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _bullets(lines: list[str]) -> list[str]:
    out = []
    for raw in lines:
        s = raw.strip()
        if s.startswith("- "):
            out.append(s[2:].strip())
    return out


def _moves_with_why(lines: list[str]) -> list[tuple[str, str]]:
    """Each next-best-move paired with the allocator's verbatim `why:` line.

    The rationale is shown as-is (not paraphrased): seeing the actual reason the
    system chose a move is what makes the suggestion trustworthy.
    """
    out: list[tuple[str, str]] = []
    action: str | None = None
    why = ""
    for raw in lines:
        s = raw.strip()
        m = re.match(r"^\d+\.\s+(.*)", s)
        if m:
            if action is not None:
                out.append((action, why))
            action, why = m.group(1).strip(), ""
        elif s.lower().startswith("why:") and action is not None:
            why = s[4:].strip()
    if action is not None:
        out.append((action, why))
    return out


def _first_match(lines: list[str], *needles: str) -> str:
    for ln in lines:
        low = ln.lower()
        if all(n in low for n in needles):
            return ln
    return ""


def _num(text: str) -> str:
    m = re.search(r"\d[\d,]*", text)
    return m.group(0) if m else ""


def _load_brief_text(root: Path) -> str | None:
    """The Brief text, never silently empty.

    Reads ``journal/inbox/brief.md`` but falls back to rendering the Brief live
    when the file is missing, empty, or caught mid-rewrite by a concurrent
    session close. Without this fallback a transient empty read makes the startup
    digest vanish, leaving only other tools' output on screen.
    """
    path = root / BRIEF_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if text.strip():
        return text
    # File absent/empty/partial — build it in-memory so the brief still shows.
    try:
        from tools.workers import build_brief

        return build_brief.render_brief(root)
    except Exception:
        return None


def _brief_age(raw_ts: str) -> tuple[str, bool]:
    """Human age of the brief and whether it is stale (older than 6h).

    The brief is precomputed at session close, so on a fresh open it can be
    hours old. Surfacing the age — and warning when stale — stops a stale brief
    from quietly steering the day's first decision.
    """
    try:
        gen_dt = datetime.fromisoformat(raw_ts)
        if gen_dt.tzinfo is None:
            gen_dt = gen_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return "", False
    delta = datetime.now(timezone.utc) - gen_dt
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "just now", False
    if mins < 60:
        age = f"{mins}m"
    elif mins < 60 * 24:
        age = f"{mins // 60}h"
    else:
        age = f"{mins // (60 * 24)}d"
    return age, mins >= 6 * 60


def render_brief_digest(root: Path) -> str | None:
    """A tight, action-first digest of the Brief. None if no usable brief.

    Ordered for fast re-entry at session start: one clear thing to start on,
    then the other moves, the ship arm, continuity ("since last session"), and
    maintenance collapsed to a single line. The verbose system hygiene lives in
    the full brief, not here.
    """
    text = _load_brief_text(root)
    if not text or not text.strip():
        return None

    gen = ""
    age_str = ""
    stale = False
    m = re.search(r"_Generated\s+([0-9T:+\-]+)", text)
    if m:
        raw_ts = m.group(1)
        gen = raw_ts[:16].replace("T", " ")
        age_str, stale = _brief_age(raw_ts)

    sections = _parse_brief_sections(text)
    changed = _bullets(sections.get("what changed", []))
    attention = _bullets(sections.get("what's stale / needs attention", []))
    ship = _bullets(sections.get("what's ready to ship", []))
    moves = _moves_with_why(sections.get("next best moves", []))

    warns = [a for a in attention if a.lower().startswith("warn")]

    bar = "─" * 60
    header = "  ExoCortex Brief"
    if gen:
        header += "   ·   " + gen
        if age_str:
            header += f"  ·  {age_str} old"
    out: list[str] = [bar, header, bar]
    if stale:
        out.append(
            f"⚠  This brief is {age_str} old — it refreshes when a session closes, "
            "or run `exocortex-brief` to rebuild it now."
        )

    # 1) The single clearest place to start, with the allocator's reason shown
    #    verbatim — the "why" is what makes the suggestion worth trusting.
    if moves:
        act, why = moves[0]
        out.append(f"▶  Start here   {act}")
        if why:
            out.append(f"   why →       {why}")
        rest = moves[1:3]
        if rest:
            out.append("   Next")
            for i, (act, why) in enumerate(rest, start=2):
                out.append(f"     {i}. {act}")
                if why:
                    out.append(f"        why → {why}")
    else:
        out.append("▶  Start here   nothing queued — `exocortex-next` to plan, or just go.")

    # 2) Ship arm — the output lane.
    if ship:
        empty = any("nothing in the ship tracker" in s.lower() for s in ship)
        if empty:
            out.append("◆  Ship:        nothing queued — `exocortex-ship add \"<title>\"`")
        else:
            out.append("◆  Ship:        " + "  ·  ".join(ship[:2]))

    # 3) Continuity — what was happening, so re-entry is cheap.
    synth = _first_match(changed, "synthesis")
    sessions = _first_match(changed, "recent sessions")
    cont_bits: list[str] = []
    if sessions:
        # keep only the most recent couple of session stamps
        tail = sessions.split(":", 1)[-1].strip()
        cont_bits.append(", ".join(p.strip() for p in tail.split(",")[:2]))
    if synth:
        syn = re.search(r"\d{4}-[WQ]?\d+", synth)
        if syn:
            cont_bits.append("synthesis " + syn.group(0))
    if cont_bits:
        out.append("•  Since last: " + "  ·  ".join(cont_bits))

    # 4) Maintenance collapsed to one honest line (full detail in the brief).
    if attention:
        cand = _num(_first_match(attention, "candidate"))
        raw = _num(_first_match(attention, "raw_inbox"))
        errs = _num(_first_match(attention, "synthesis errors"))
        bits = []
        if cand:
            bits.append(f"{cand} candidates")
        if raw:
            bits.append(f"{raw} raw waiting")
        if errs:
            bits.append(f"{errs} synthesis errors")
        tail = ("  —  " + ", ".join(bits)) if bits else ""
        out.append(f"!  Maintenance: {len(warns)} warnings{tail}  —  `exocortex-health`")

    out.append(f"   Full brief:  {BRIEF_FILE}   ·   score breakdown: exocortex-next --why")
    out.append(bar)
    return "\n".join(out)


def main() -> int:
    try:
        import exocortex_wrapper as w

        root = w.exocortex_root()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[exo] hook-context: skipped ({exc})", file=sys.stderr)
        return 0

    # 1) Full model-facing manifest — ONLY for sessions the terminal wrapper did
    #    not already cover. The wrapper injects the same `build_context_prompt`
    #    manifest via `--append-system-prompt`, so re-printing it here in a
    #    wrapped session duplicates a large block on screen (the "Scope/Authority"
    #    wall) and feeds the model the manifest twice. The wrapper sets
    #    EXOCORTEX_SESSION_ID in the child env before launch; its presence means
    #    "wrapped" — in that case we skip straight to the human-readable brief.
    #    Desktop/web/sub-agent sessions have no wrapper, so they still get it.
    wrapped = bool(os.environ.get("EXOCORTEX_SESSION_ID"))
    if not wrapped:
        try:
            cwd = Path.cwd()
            domain, project = w.detect_domain_project(root, cwd)
            agent = w.default_agent(domain, project, cwd, root)
            mode = w.default_mode(agent)
            context = w.collect_context(root, cwd, agent, mode)
            print(w.build_context_prompt(context))
        except Exception as exc:
            print(f"[exo] hook-context: skipped ({exc})", file=sys.stderr)

    # 2) Human-readable digest LAST — printed at the bottom so it is the final
    #    thing on screen at session start (no scrolling to the top to read it).
    try:
        digest = render_brief_digest(root)
        if digest:
            print()
            print(digest)
    except Exception as exc:  # pragma: no cover - digest must never break the hook
        print(f"[exo] hook-context: brief digest skipped ({exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
