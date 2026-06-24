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

import re
import sys
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


def render_brief_digest(root: Path) -> str | None:
    """A tight, action-first digest of the Brief. None if no usable brief.

    Ordered for fast re-entry at session start: one clear thing to start on,
    then the other moves, the ship arm, continuity ("since last session"), and
    maintenance collapsed to a single line. The verbose system hygiene lives in
    the full brief, not here.
    """
    path = root / BRIEF_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None

    gen = ""
    m = re.search(r"_Generated\s+([0-9T:+\-]+)", text)
    if m:
        gen = m.group(1)[:16].replace("T", " ")

    sections = _parse_brief_sections(text)
    changed = _bullets(sections.get("what changed", []))
    attention = _bullets(sections.get("what's stale / needs attention", []))
    ship = _bullets(sections.get("what's ready to ship", []))
    moves = _moves_with_why(sections.get("next best moves", []))

    warns = [a for a in attention if a.lower().startswith("warn")]

    bar = "─" * 60
    out: list[str] = [bar, f"  ExoCortex Brief{('   ·   ' + gen) if gen else ''}", bar]

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

    # 1) Full model-facing manifest first.
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
