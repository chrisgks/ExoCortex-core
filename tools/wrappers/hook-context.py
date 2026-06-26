#!/usr/bin/env python3
"""SessionStart hook: surface the ExoCortex Brief and context at session start.

The hook emits a single SessionStart JSON object on stdout:

- ``systemMessage``: the human-readable Brief digest. This is the ONLY hook
  field the harness renders DIRECTLY to the user's terminal. Plain SessionStart
  stdout is added to the model's context but never shown on screen — which is
  why earlier versions printed a correct brief that the user never saw.
- ``hookSpecificOutput.additionalContext``: the same brief plus, for sessions
  not already covered by the terminal wrapper, the full context manifest — so
  the model also has it (harmless if the wrapper injects the manifest too).
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BRIEF_FILE = "journal/inbox/brief.md"

# The digest is delivered via the SessionStart hook's `systemMessage`, which the
# harness renders to the terminal. Whether it also renders ANSI colour is not
# guaranteed across versions — if colour shows up as raw escape codes on screen,
# flip USE_COLOR to False and the layout still reads cleanly in plain text.
USE_COLOR = True
WIDTH = 62


def _c(code: str, s: str) -> str:
    """Wrap text in an ANSI SGR code (no-op when colour is disabled)."""
    return f"\033[{code}m{s}\033[0m" if USE_COLOR else s


def _fit(s: str, n: int) -> str:
    """Truncate to n display chars with an ellipsis so lines never wrap. The full
    text always lives in the brief file; the digest is a glance surface."""
    s = s.strip().rstrip(".")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _wrap(s: str, width: int, indent: str, max_lines: int = 2) -> list[str]:
    """Word-wrap a short narrative to at most max_lines, ellipsising overflow."""
    words = s.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(indent + cur)
            cur = w
            if len(lines) == max_lines:
                break
        else:
            cur = f"{cur} {w}".strip()
    if cur and len(lines) < max_lines:
        lines.append(indent + cur)
    used = sum(len(line) - len(indent) for line in lines)
    if len(lines) == max_lines and used < len(s):
        lines[-1] = lines[-1].rstrip().rstrip(".") + "…"
    return lines


def _bullet_lines(text: str, width: int, lead: str = "   • ", cont: str = "     ") -> list[str]:
    """Render one bullet, wrapping with a hanging indent so the FULL text shows
    (the brief is for re-orientation — never silently truncate the content)."""
    words = text.split()
    lines: list[str] = []
    cur = lead
    for w in words:
        started = cur not in (lead, cont)
        trial = f"{cur} {w}" if started else cur + w
        if started and len(trial) > width:
            lines.append(cur)
            cur = cont + w
        else:
            cur = trial
    if cur not in (lead, cont):
        lines.append(cur)
    return lines


def _latest_weekly_text(root: Path) -> str:
    d = root / "journal" / "weekly"
    if not d.exists():
        return ""
    # Only dated synthesis pages (YYYY-Www.md) — not README or other files.
    files = sorted(f for f in d.glob("*.md") if re.fullmatch(r"\d{4}-W\d+", f.stem))
    if not files:
        return ""
    try:
        return files[-1].read_text(encoding="utf-8")
    except OSError:
        return ""


def _intro_of(text: str) -> str:
    """The opening narrative paragraph of a synthesis (what's been happening)."""
    for block in re.split(r"\n\s*\n", text):
        b = block.strip()
        if not b or b.startswith(("#", "- ", "generated_at", "sources", "confidence")):
            continue
        return " ".join(b.split())
    return ""


def _section_bullets(text: str, heading: str, n: int) -> list[str]:
    """Top n bullets under a `## heading` in a synthesis page."""
    m = re.search(rf"##\s*{re.escape(heading)}\s*\n(.*?)(?:\n##|\Z)", text, re.S | re.I)
    if not m:
        return []
    out: list[str] = []
    for ln in m.group(1).splitlines():
        ln = ln.strip()
        if ln.startswith("- "):
            out.append(" ".join(ln[2:].split()))
            if len(out) >= n:
                break
    return out


def _open_loops(root: Path, n: int = 3) -> list[str]:
    """Real open commitments/threads — skips auto-captured stale state notes."""
    p = root / "system" / "OPEN LOOPS.md"
    if not p.exists():
        return []
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("- ") or "(recorded:" in line:
            continue
        out.append(re.sub(r"\s*\(recorded:.*?\)", "", line[2:]).strip())
        if len(out) >= n:
            break
    return out


_DIM, _BOLD, _CYAN, _YEL, _TITLE = "2", "1", "36", "33", "1;36"


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
    """The Brief text, rendered fresh on every session start and never silently
    empty.

    The Brief is cheap to render now (sub-second — the hygiene scan no longer
    walks the journal or nested project repos), so we render it fresh and persist
    it on every open. This is the hard guarantee the user asked for: the startup
    digest reflects current state on *absolutely every* session start, never a
    stale snapshot, and independent of whether the previous session's detached
    background worker has finished yet.

    Robustness is layered: render-and-persist first; on any render failure, fall
    back to the last-good ``brief.md`` on disk (its age is surfaced, so a stale
    fallback is visible, not silent); only if both fail do we give up. So the
    digest never simply vanishes.
    """
    path = root / BRIEF_FILE
    try:
        from tools.workers import build_brief

        build_brief.write_brief(root)  # render fresh + atomic persist (single render)
    except Exception:
        pass
    try:
        text = path.read_text(encoding="utf-8")
        if text.strip():
            return text
    except OSError:
        pass
    # Persist failed and no usable file on disk — last resort: render in-memory.
    try:
        from tools.workers import build_brief

        text = build_brief.render_brief(root)
        return text if text and text.strip() else None
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
    attention = _bullets(sections.get("what's stale / needs attention", []))
    ship = _bullets(sections.get("what's ready to ship", []))
    warns = [a for a in attention if a.lower().startswith("warn")]

    # The brief exists to kill re-orientation cost: open a session and reload
    # exactly where you were. So lead with substance — what's been happening,
    # what you worked on, the open loops and live threads — pulled from the
    # weekly synthesis and the open-loops ledger. System counters are demoted to
    # one dim footer line; they are not what you re-orient on.
    wk = _latest_weekly_text(root)
    where = _intro_of(wk)
    worked = _section_bullets(wk, "Work & Projects", 3)
    threads = _section_bullets(wk, "Ideas & Threads", 3)
    loops = _open_loops(root, 3)

    rule = _c(_DIM, "─" * WIDTH)
    title = "◆ ExoCortex Brief"
    right = ""
    if gen:
        clock = gen[11:16] if len(gen) >= 16 else gen
        right = clock + (f" · {age_str}" if age_str else "")
    pad = max(1, WIDTH - len("  " + title) - len(right))
    out: list[str] = [
        rule,
        "  " + _c(_TITLE, title) + (" " * pad) + _c(_DIM, right),
        rule,
        "",
    ]
    if stale:
        out.append("  " + _c(_YEL, f"⚠ {age_str} old — run `exocortex-brief` to rebuild"))
        out.append("")

    def section(label: str, bullets: list[str]) -> None:
        if not bullets:
            return
        out.append("  " + _c(_BOLD, label))
        for b in bullets:
            out.extend(_bullet_lines(b, WIDTH))
        out.append("")

    # Where things stand — the full narrative, wrapped, never truncated.
    if where:
        out.append("  " + _c(_BOLD, "WHERE YOU LEFT OFF"))
        out.extend(_wrap(where, WIDTH - 2, "  ", max_lines=20))
        out.append("")

    section("WORKED ON", worked)
    section("OPEN LOOPS", loops)
    section("THREADS", threads)

    if ship:
        empty = any("nothing in the ship tracker" in s.lower() for s in ship)
        out.append("  " + _c(_BOLD, "SHIP   ")
                   + ('nothing queued — exocortex-ship add "<title>"'
                      if empty else "  ".join(ship[:2])))
        out.append("")

    # System status: one demoted, dim line — present but not the headline.
    sys_bits: list[str] = []
    if warns:
        sys_bits.append(f"{len(warns)} warnings")
    cand = _num(_first_match(attention, "candidate"))
    if cand:
        sys_bits.append(f"{cand} candidates")
    prefix = (" · ".join(sys_bits) + "   ·   ") if sys_bits else ""
    out.append("  " + _c(_DIM, f"{prefix}full · {BRIEF_FILE}"))
    out.append(rule)
    return "\n".join(out)


def main() -> int:
    try:
        import exocortex_wrapper as w

        root = w.exocortex_root()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[exo] hook-context: skipped ({exc})", file=sys.stderr)
        return 0

    # Model-facing context goes into additionalContext (the model reads it, the
    # user does not). The full manifest is added ONLY for sessions the terminal
    # wrapper did not already cover — the wrapper injects the same
    # `build_context_prompt` via `--append-system-prompt`, so repeating it in a
    # wrapped session feeds the model the manifest twice. EXOCORTEX_SESSION_ID in
    # the child env marks a wrapped session; desktop/web/sub-agent sessions have
    # no wrapper, so they still get the manifest here.
    context_parts: list[str] = []
    wrapped = bool(os.environ.get("EXOCORTEX_SESSION_ID"))
    if not wrapped:
        try:
            cwd = Path.cwd()
            domain, project = w.detect_domain_project(root, cwd)
            agent = w.default_agent(domain, project, cwd, root)
            mode = w.default_mode(agent)
            context = w.collect_context(root, cwd, agent, mode)
            context_parts.append(w.build_context_prompt(context))
        except Exception as exc:
            print(f"[exo] hook-context: skipped ({exc})", file=sys.stderr)

    digest = None
    try:
        digest = render_brief_digest(root)
    except Exception as exc:  # pragma: no cover - digest must never break the hook
        print(f"[exo] hook-context: brief digest skipped ({exc})", file=sys.stderr)
    if digest:
        context_parts.append(digest)

    # Emit one SessionStart payload. systemMessage is what the user actually SEES
    # on screen; additionalContext is what the model reads. Plain stdout would
    # only reach the model — the whole point of this fix is the on-screen brief.
    payload: dict = {"hookSpecificOutput": {"hookEventName": "SessionStart"}}
    if context_parts:
        payload["hookSpecificOutput"]["additionalContext"] = "\n\n".join(context_parts)
    if digest:
        payload["systemMessage"] = digest
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
