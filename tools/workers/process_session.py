#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fcntl

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workers import intent_review


ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
BOOTSTRAP_FIELD_RE = re.compile(
    r"^\s*-\s*(last_updated|sources|sleep_status|recovery|energy(_now)?|stress_load(_now)?|"
    r"cognitive_readiness(_now)?|emotional_state(_now)?|exercise_status|carryover_fatigue|"
    r"carryover_stress|sleep_trend|recovery_trend|load_trend|recent_window_days|confidence|"
    r"adaptation_confidence|response_pacing|question_load|scope_bias|tone|should_ask_checkin|"
    r"recommended_checkin)\s*:",
    re.I,
)
HEALTH_PLACEHOLDER_RE = re.compile(
    r"^\s*-?\s*(sleep_status|recovery|energy(_now)?|stress_load(_now)?|cognitive_readiness(_now)?|"
    r"emotional_state(_now)?|exercise_status|carryover_fatigue|carryover_stress|sleep_trend|"
    r"recovery_trend|load_trend|confidence|adaptation_confidence|response_pacing|question_load|"
    r"scope_bias|tone|should_ask_checkin|recommended_checkin|last_updated|sources|recent_window_days)"
    r"\s*[:=]\s*(unknown|low|normal|reflective|yes|manual bootstrap|[0-9-]+)$",
    re.I,
)
PROMOTION_KEYS = (
    "memory_candidates",
    "workflow_candidates",
    "skill_candidates",
    "decision_rule_candidates",
    "intent_candidates",
    "self_model_candidates",
    "persona_candidates",
    "question_template_candidates",
)
CANDIDATE_TYPE_ORDER = (
    "memory",
    "workflow",
    "decision_rule",
    "skill",
    "intent",
    "self_model",
    "persona",
    "question_template",
    "open_question",
)
QUEUE_TITLES = {
    "memory": "Pending Memory Candidates",
    "workflow": "Pending Workflow Candidates",
    "decision_rule": "Pending Rule Candidates",
    "skill": "Pending Skill Candidates",
    "intent": "Pending Intent Candidates",
    "self_model": "Pending Self-Model Candidates",
    "persona": "Pending Persona Calibration Candidates",
    "question": "Pending Question Candidates",
}
CONFIDENCE_SCORES = {"low": 1, "medium": 2, "high": 3}
WORKER_PROGRESS_PREFIX = "EXOCORTEX_PROGRESS|"
NO_CANDIDATE_SOURCE = "none"
MODEL_CANDIDATE_SOURCE = "model"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def emit_progress(phase: str, message: str) -> None:
    if os.environ.get("EXOCORTEX_PROGRESS") != "1":
        return
    print(f"{WORKER_PROGRESS_PREFIX}{phase}|{message}", file=sys.stderr, flush=True)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def extract_transcript_entries(text: str) -> list[dict[str, str]]:
    cleaned = strip_ansi(text)
    if "## Stream" in cleaned:
        cleaned = cleaned.split("## Stream", 1)[1]
    elif "## Output" in cleaned:
        cleaned = cleaned.split("## Output", 1)[1]

    entries: list[dict[str, str]] = []
    for raw in cleaned.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        match = re.match(r"^\[(user|tool)\]\s?(.*)$", line)
        if match:
            entries.append({"role": match.group(1), "text": match.group(2)})
        else:
            entries.append({"role": "tool", "text": line})
    return entries


def shape_claude_jsonl_event(event: dict[str, Any]) -> tuple[str, str] | None:
    """Map a Claude Code session-jsonl event to ``(role, text)`` or ``None`` to drop.

    Mirrors the shape produced by the PTY-tee transcript so downstream
    summarization sees a uniform stream regardless of capture source. Skips
    private content (thinking blocks), tool_use metadata, and system events.
    """
    event_type = event.get("type")
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if event_type == "user":
        if isinstance(content, str):
            text = content.strip()
            return ("user", text) if text else None
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    block_text = block.get("text", "").strip()
                    if block_text:
                        parts.append(block_text)
            joined = "\n".join(parts).strip()
            return ("user", joined) if joined else None
    elif event_type == "assistant":
        if isinstance(content, str):
            text = content.strip()
            return ("tool", text) if text else None
        if isinstance(content, list):
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    block_text = block.get("text", "").strip()
                    if block_text:
                        parts.append(block_text)
            joined = "\n".join(parts).strip()
            return ("tool", joined) if joined else None
    return None


CLAUDE_MEM_DB_PATH = Path.home() / ".claude-mem" / "claude-mem.db"


def claude_session_uuid_from_jsonl(path: Path) -> str | None:
    """Claude Code names session files ``<sessionId>.jsonl``; this returns
    the UUID component or ``None`` when the filename does not match.
    """
    stem = path.stem
    return stem if len(stem) == 36 and stem.count("-") == 4 else None


def load_claude_mem_session(content_session_uuid: str) -> tuple[str, list[dict[str, str]]] | None:
    """Read claude-mem's compressed view of a Claude Code session.

    Looks up ``sdk_sessions`` by the Claude Code session UUID to find the
    associated ``memory_session_id``, then pulls the ordered ``user_prompts``
    and ``observations`` rows. Returns ``None`` when the database is missing,
    the session is not yet linked, or no observations exist (claude-mem may
    still be compressing — caller should fall back to the raw transcript).
    """
    if not CLAUDE_MEM_DB_PATH.exists():
        return None
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{CLAUDE_MEM_DB_PATH}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            "SELECT memory_session_id FROM sdk_sessions WHERE content_session_id = ? LIMIT 1",
            (content_session_uuid,),
        )
        row = cur.fetchone()
        memory_session_id = row[0] if row else None
        cur.execute(
            "SELECT prompt_text, created_at_epoch FROM user_prompts "
            "WHERE content_session_id = ? ORDER BY prompt_number ASC",
            (content_session_uuid,),
        )
        prompts = cur.fetchall()
        observations: list[tuple[Any, ...]] = []
        if memory_session_id:
            cur.execute(
                "SELECT type, title, subtitle, narrative, text, created_at_epoch "
                "FROM observations WHERE memory_session_id = ? ORDER BY created_at_epoch ASC",
                (memory_session_id,),
            )
            observations = cur.fetchall()
        con.close()
    except Exception:
        return None

    # Require at least one compressed observation. A prompts-only result means
    # claude-mem has logged the session but not yet compressed it — that view
    # drops every assistant/tool turn and produces empty "None extracted"
    # summaries. Returning None here lets load_session_transcript fall through
    # to Claude's native .jsonl, which carries the full conversation.
    if not observations:
        return None

    entries: list[dict[str, str]] = []
    text_lines: list[str] = []
    merged: list[tuple[int, str, str]] = []
    for prompt_text, epoch_ms in prompts:
        if not prompt_text:
            continue
        merged.append((int(epoch_ms or 0), "user", str(prompt_text).strip()))
    for obs_type, title, subtitle, narrative, text, epoch_ms in observations:
        body_parts: list[str] = []
        if title:
            label = f"[{obs_type}] {title}" if obs_type else title
            body_parts.append(label)
        if subtitle and subtitle != title:
            body_parts.append(str(subtitle))
        if narrative:
            body_parts.append(str(narrative))
        elif text and not narrative:
            body_parts.append(str(text))
        body = "\n".join(part for part in body_parts if part).strip()
        if not body:
            continue
        merged.append((int(epoch_ms or 0), "tool", body))
    merged.sort(key=lambda item: item[0])
    for _, role, body in merged:
        entries.append({"role": role, "text": body})
        text_lines.append(f"[{role}] {body}")
    if not entries:
        return None
    return "\n".join(text_lines) + "\n", entries


def parse_claude_jsonl_transcript(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Read a Claude Code session ``.jsonl`` and produce ``(text, entries)``
    in the same shape ``extract_transcript_entries`` returns.
    """
    entries: list[dict[str, str]] = []
    text_lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                shaped = shape_claude_jsonl_event(event)
                if shaped is None:
                    continue
                role, text = shaped
                entries.append({"role": role, "text": text})
                text_lines.append(f"[{role}] {text}")
    except OSError:
        return "", []
    return "\n".join(text_lines) + ("\n" if text_lines else ""), entries


def load_session_transcript(
    root: Path,
    manifest: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    """Resolve transcript text and entries based on the manifest's capture
    strategy. The fallback chain (most-compressed → least) for the
    ``claude-jsonl`` strategy is:

    1. claude-mem compressed observations (preferred — already LLM-compressed)
    2. raw Claude Code session ``.jsonl`` parsed into user/assistant text
    3. wrapper's PTY-tee transcript

    Other strategies always read the PTY-tee transcript.
    """
    strategy = manifest.get("capture_strategy", "pty-tee")
    if strategy == "claude-jsonl":
        native_path = _find_claude_jsonl_for_manifest(manifest)
        if native_path is not None:
            session_uuid = claude_session_uuid_from_jsonl(native_path)
            if session_uuid:
                claude_mem_result = load_claude_mem_session(session_uuid)
                if claude_mem_result is not None:
                    return claude_mem_result
            if native_path.exists():
                text, entries = parse_claude_jsonl_transcript(native_path)
                if entries:
                    return text, entries
        # Fall through to PTY-tee fallback below.
    transcript_path = root / manifest.get("transcript_path", "")
    transcript_text = (
        transcript_path.read_text(encoding="utf-8")
        if transcript_path.exists()
        else ""
    )
    return transcript_text, extract_transcript_entries(transcript_text)


def _find_claude_jsonl_for_manifest(manifest: dict[str, Any]) -> Path | None:
    try:
        from tools.wrappers.exocortex_wrapper import find_claude_session_jsonl
    except Exception:
        return None
    cwd_value = manifest.get("cwd")
    if not cwd_value:
        return None
    started_at_epoch = manifest.get("started_at_epoch")
    epoch_int: int | None
    if isinstance(started_at_epoch, (int, float)):
        epoch_int = int(started_at_epoch)
    else:
        epoch_int = None
    return find_claude_session_jsonl(Path(cwd_value), epoch_int)


def append_locked_once(path: Path, block: str, session_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = f"- session_id: `{session_id}`"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing = handle.read()
        if marker not in existing:
            handle.seek(0, 2)
            handle.write(block)
            if not block.endswith("\n"):
                handle.write("\n")
            # Flush to disk before releasing the lock. Otherwise a second
            # process can acquire the lock and read stale content (missing this
            # marker) while the write is still in Python's buffer, then write a
            # duplicate. This is the race the suite catches on Linux CI.
            handle.flush()
            os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def duration_minutes(started: str | None, ended: str | None) -> str:
    if not started or not ended:
        return "unknown"
    try:
        start = datetime.fromisoformat(started)
        end = datetime.fromisoformat(ended)
    except ValueError:
        return "unknown"
    delta = end - start
    minutes = max(int(delta.total_seconds() // 60), 0)
    return str(minutes)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def merge_unique(primary: list[str], secondary: list[str], limit: int = 5) -> list[str]:
    return dedupe_keep_order(primary + secondary)[:limit]


def bullet_section(title: str, items: list[str]) -> list[str]:
    lines = [title, ""]
    if items:
        lines.extend(f"- {item}" for item in items)
    else:
        lines.append("- None extracted.")
    lines.append("")
    return lines


def format_entry(entry: dict[str, str]) -> str:
    return f"{entry['role']}: {entry['text']}"


def normalize_candidate_text(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"`+", "", lowered)
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def clear_candidate_lists(data: dict[str, Any]) -> dict[str, Any]:
    cleared = dict(data)
    for key in PROMOTION_KEYS:
        cleared[key] = []
    cleared["candidate_source"] = NO_CANDIDATE_SOURCE
    return cleared


def log_synthesis_error(root: Path, manifest: dict[str, Any], failures: list[str]) -> None:
    if not failures:
        return
    session_id = manifest.get("session_id", "unknown")
    generated_at = manifest.get("ended_at") or manifest.get("started_at") or now_iso()
    block = "\n".join(
        [
            f"## {generated_at} synthesis failure",
            "",
            f"- session_id: `{session_id}`",
            f"- active_agent: `{manifest.get('active_agent')}`",
            f"- active_mode: `{manifest.get('active_mode')}`",
            "- effect: summary fell back to heuristics; no promotion candidates were written",
            "- failures:",
            *[f"  - {failure}" for failure in failures],
            "",
        ]
    )
    append_locked_once(root / "journal" / "inbox" / "synthesis-errors.md", block, session_id)


def is_candidate_text_noise(text: str) -> bool:
    stripped = strip_ansi(text).strip()
    if not stripped:
        return True
    if is_noise_line(stripped):
        return True
    if len(stripped) < 12:
        return True
    if re.match(r"^-{1,2}[a-zA-Z0-9][\w-]*(?:[ ,|/]+-{1,2}[\w-]+)*(?:\s+<[^>]+>)?(?:\s{2,}|\s*$)", stripped):
        return True
    if re.match(r"^[a-z0-9-]+\s{2,}[A-Z]", stripped):
        return True
    if re.search(r"\b(usage:|options:|commands:|arguments:|show this help message)\b", stripped, re.I):
        return True
    if re.search(r"\b(--config|--help|--version|--model|--sandbox|--profile|--cd)\b", stripped) and len(stripped) < 160:
        return True
    if stripped.count("`") >= 6 and re.search(r"\b(flag|option|argument|command)\b", stripped, re.I):
        return True
    return False


def is_noise_line(line: str) -> bool:
    stripped = line.lstrip()
    noise_prefixes = (
        "[exo]",
        "EXOCORTEX_PROGRESS|",
        "ExoCortex context bootstrap:",
        "- Scope:",
        "- Authority:",
        "- Active level:",
        "- Active agent:",
        "- Active mode:",
        "- Working directory:",
        "- Domain:",
        "- Project:",
        "- Startup brief from authoritative files:",
        "- Context files to read next:",
        "- Reusable context:",
        "- Health summary:",
        "- Operating rules:",
        "- Visible context files to read first:",
        "- Weighted reusable context to keep in mind:",
        "- Default operating rule:",
        "- If you produce durable learnings",
        "- session_id:",
        "- tool:",
        "- cwd:",
        "- started_at:",
        "# Session Transcript",
        "## Output",
        "## Stream",
        "- root:",
        "- system:",
        "- domain:",
        "- project:",
        "- Health overlay for this session:",
        "Health check-in guidance:",
        "- Health check-in guidance:",
        "memory:",
        "workflow:",
        "decision_rule:",
        "persona:",
        "self_model:",
        "- brief:",
        "- reuse:",
        "- health:",
        "brief:",
        "reuse:",
        "health:",
    )
    if any(stripped.startswith(prefix) for prefix in noise_prefixes):
        return True
    if BOOTSTRAP_FIELD_RE.match(stripped):
        return True
    if stripped.startswith("--append-system-prompt ExoCortex context bootstrap:"):
        return True
    if HEALTH_PLACEHOLDER_RE.match(stripped):
        return True
    return False


def informative_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    return [entry for entry in entries if not is_noise_line(entry["text"])]


def pick_matches(
    entries: list[dict[str, str]],
    patterns: tuple[re.Pattern[str], ...],
    roles: set[str] | None = None,
    limit: int = 5,
) -> list[str]:
    matches = []
    for entry in entries:
        if roles and entry["role"] not in roles:
            continue
        line = entry["text"]
        if any(pattern.search(line) for pattern in patterns):
            matches.append(line)
    return dedupe_keep_order(matches)[:limit]


def derive_memory_candidates(entries: list[dict[str, str]]) -> list[str]:
    return pick_matches(
        entries,
        (
            re.compile(r"\b(prefer|preferred|want|wants|need|needs|value|values|important|care about|works best|helps|benefits from|avoid|dislike|like)\b", re.I),
        ),
        roles={"user", "tool"},
        limit=5,
    )


def derive_persona_candidates(entries: list[dict[str, str]]) -> list[str]:
    return pick_matches(
        entries,
        (
            re.compile(r"\b(clear|clarity|direct|structured?|structure|challenge|reflective|question|framing|pace|pacing|next action|honest|candid)\b", re.I),
        ),
        roles={"user", "tool"},
        limit=5,
    )


def reflection_items(
    completed: list[str],
    decisions: list[str],
    follow_ups: list[str],
    memory_candidates: list[str],
    self_model_candidates: list[str],
    persona_candidates: list[str],
    workflow_candidates: list[str],
    info: list[dict[str, str]],
) -> dict[str, list[str]]:
    user_lines = [entry["text"] for entry in info if entry["role"] == "user"]
    tool_lines = [entry["text"] for entry in info if entry["role"] == "tool"]
    what_mattered = merge_unique(completed, decisions, limit=3)
    if not what_mattered:
        what_mattered = dedupe_keep_order(user_lines[:1] + tool_lines[:2])[:3]

    repeated_patterns = dedupe_keep_order(self_model_candidates + workflow_candidates)[:4]
    model_updates = dedupe_keep_order(memory_candidates + self_model_candidates + persona_candidates)[:4]
    easier_next_time = dedupe_keep_order(workflow_candidates + follow_ups)[:5]
    return {
        "what_mattered": what_mattered,
        "repeated_patterns": repeated_patterns,
        "model_updates": model_updates,
        "easier_next_time": easier_next_time,
    }


def heuristic_summary_data(
    manifest: dict[str, Any],
    transcript_entries: list[dict[str, str]],
) -> dict[str, Any]:
    info = informative_entries(transcript_entries)
    health_snapshot = manifest.get("health_snapshot", {}) or {}
    completed = pick_matches(
        info,
        (
            re.compile(r"\b(implemented|added|created|updated|fixed|ran|verified|wrote|built|scaffolded|captured|summarized|generated)\b", re.I),
            re.compile(r"\b(done|completed|finished)\b", re.I),
        ),
        roles={"tool"},
        limit=6,
    )
    decisions = pick_matches(
        info,
        (
            re.compile(r"\b(decide|decided|decision|choose|chosen|selected|prefer|preferred|will use|should use)\b", re.I),
        ),
        roles={"tool", "user"},
        limit=6,
    )
    open_questions = pick_matches(
        info,
        (
            re.compile(r"\?$"),
            re.compile(r"\b(open question|unclear|unknown|not sure|need to decide|need to clarify)\b", re.I),
        ),
        roles={"tool", "user"},
        limit=6,
    )
    follow_ups = pick_matches(
        info,
        (
            re.compile(r"\b(next|follow[- ]?up|todo|to do|need to|should next|remaining|later)\b", re.I),
        ),
        roles={"tool"},
        limit=6,
    )
    intent_candidates = pick_matches(
        info,
        (
            re.compile(r"\b(we will|we'll|i will|i'll|we should|it would be nice|eventually|later we|later on|at some point|in future|through cron jobs|cron jobs|automation[s]? through)\b", re.I),
            re.compile(r"\b(want this to|should become|meant to|going to)\b", re.I),
        ),
        roles={"tool", "user"},
        limit=6,
    )
    workflow_candidates = pick_matches(
        info,
        (
            re.compile(r"\b(first|then|after that|workflow|process|step|checklist)\b", re.I),
        ),
        roles={"tool"},
        limit=5,
    )
    skill_candidates = pick_matches(
        info,
        (
            re.compile(r"\b(script|template|checklist|wrapper|worker|command|automation)\b", re.I),
        ),
        roles={"tool"},
        limit=5,
    )
    decision_rule_candidates = pick_matches(
        info,
        (
            re.compile(r"\b(always|never|prefer|if .* then|default|rule of thumb)\b", re.I),
        ),
        roles={"tool", "user"},
        limit=5,
    )
    self_model_candidates = pick_matches(
        info,
        (
            re.compile(r"\b(user|you|clarity|friction|blocked|optimi[sz]|feel|feeling|want|struggle|stuck|prefer|motivat|systems-oriented|structure)\b", re.I),
        ),
        roles={"tool", "user"},
        limit=5,
    )
    question_template_candidates = pick_matches(
        info,
        (
            re.compile(r"\?$"),
        ),
        roles={"tool", "user"},
        limit=5,
    )
    memory_candidates = derive_memory_candidates(info)
    persona_candidates = derive_persona_candidates(info)

    confidence = "medium" if info else "low"
    if manifest.get("active_mode") == "conversation" and info:
        confidence = "medium"
    rationale = (
        "Heuristic semantic extraction from wrapper-captured session stream. Upgrade to model-backed summarization by "
        "setting EXOCORTEX_SUMMARIZER_PROVIDER=claude and EXOCORTEX_REAL_CLAUDE to the underlying CLI path."
    )
    summary_sentence = (
        f"Session in `{manifest.get('cwd')}` using agent `{manifest['active_agent']}` in mode "
        f"`{manifest['active_mode']}`."
    )
    if completed:
        summary_sentence += f" Detected concrete activity around: {completed[0]}"
    elif decisions:
        summary_sentence += f" Detected a likely decision signal: {decisions[0]}"
    elif manifest.get("active_mode") == "conversation" and self_model_candidates:
        summary_sentence += f" Conversation appears to contain reflective signal such as: {self_model_candidates[0]}"
    elif info:
        summary_sentence += f" Most informative line: {format_entry(info[0])}"
    else:
        summary_sentence += " No meaningful session content was extracted."
    if health_snapshot:
        interesting = []
        for key in (
            "sleep_status",
            "recovery",
            "energy_now",
            "stress_load_now",
            "cognitive_readiness_now",
            "emotional_state_now",
            "carryover_fatigue",
            "carryover_stress",
            "sleep_trend",
            "recovery_trend",
            "load_trend",
        ):
            value = health_snapshot.get(key)
            if value and value != "unknown":
                interesting.append(f"{key}={value}")
        if interesting:
            summary_sentence += " Health context in play: " + ", ".join(interesting[:3]) + "."
        elif health_snapshot.get("confidence", "").lower() == "low":
            summary_sentence += " Health overlay was low-confidence, so a brief user check-in may be warranted."

    reflection = reflection_items(
        completed,
        decisions,
        follow_ups,
        memory_candidates,
        self_model_candidates,
        persona_candidates,
        workflow_candidates,
        info,
    )

    return {
        "summary": summary_sentence,
        "completed_tasks": completed,
        "decisions": decisions,
        "open_questions": open_questions,
        "follow_ups": follow_ups or ["Review the transcript and context file if this session matters."],
        "signals": [format_entry(entry) for entry in info[:5]],
        "confidence": confidence,
        "rationale": rationale,
        "memory_candidates": memory_candidates,
        "workflow_candidates": workflow_candidates,
        "skill_candidates": skill_candidates,
        "decision_rule_candidates": decision_rule_candidates,
        "intent_candidates": intent_candidates,
        "self_model_candidates": self_model_candidates,
        "persona_candidates": persona_candidates,
        "question_template_candidates": question_template_candidates,
        "health_signals": [f"{k}={v}" for k, v in health_snapshot.items()],
        **reflection,
    }


def find_real_binary(tool: str, root: Path) -> str:
    env_name = f"EXOCORTEX_REAL_{tool.upper()}"
    if env_name in os.environ:
        return os.environ[env_name]

    wrapper_dir = root / "tools" / "wrappers" / "bin"
    path_items = [
        item
        for item in os.environ.get("PATH", "").split(os.pathsep)
        if item and Path(item).resolve() != wrapper_dir.resolve()
    ]
    real = shutil.which(tool, path=os.pathsep.join(path_items))
    if not real:
        raise RuntimeError(f"Could not locate underlying binary for {tool}. Set {env_name}.")
    return real


def summary_prompt_template(root: Path) -> str:
    prompt_path = root / "tools" / "prompts" / "session_summary.md"
    return prompt_path.read_text(encoding="utf-8")


def model_summary_schema() -> dict[str, Any]:
    array = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "completed_tasks": array,
            "decisions": array,
            "open_questions": array,
            "follow_ups": array,
            "signals": array,
            "health_signals": array,
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "rationale": {"type": "string"},
            "memory_candidates": array,
            "workflow_candidates": array,
            "skill_candidates": array,
            "decision_rule_candidates": array,
            "intent_candidates": array,
            "self_model_candidates": array,
            "persona_candidates": array,
            "question_template_candidates": array,
            "what_mattered": array,
            "repeated_patterns": array,
            "model_updates": array,
            "easier_next_time": array,
        },
        "required": [
            "summary",
            "completed_tasks",
            "decisions",
            "open_questions",
            "follow_ups",
            "signals",
            "health_signals",
            "confidence",
            "rationale",
            "memory_candidates",
            "workflow_candidates",
            "skill_candidates",
            "decision_rule_candidates",
            "intent_candidates",
            "self_model_candidates",
            "persona_candidates",
            "question_template_candidates",
            "what_mattered",
            "repeated_patterns",
            "model_updates",
            "easier_next_time",
        ],
        "additionalProperties": False,
    }


# Axis fields a period synthesis (week/month/quarter) organizes patterns into,
# in render order, paired with their markdown headers.
PERIOD_AXES: tuple[tuple[str, str], ...] = (
    ("work_and_projects", "## Work & Projects"),
    ("how_you_think", "## How You Think"),
    ("working_with_me", "## Working With Me"),
    ("ideas_and_threads", "## Ideas & Threads"),
    ("open_threads", "## Open Threads"),
    ("evolution", "## Evolution"),
)


def period_synthesis_schema() -> dict[str, Any]:
    array = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "narrative": {"type": "string"},
            "work_and_projects": array,
            "how_you_think": array,
            "working_with_me": array,
            "ideas_and_threads": array,
            "open_threads": array,
            "evolution": array,
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": [
            "narrative",
            "work_and_projects",
            "how_you_think",
            "working_with_me",
            "ideas_and_threads",
            "open_threads",
            "evolution",
            "confidence",
        ],
        "additionalProperties": False,
    }


def _extract_claude_schema_payload(raw: Any) -> dict[str, Any]:
    """Pull the schema-matching object out of ``claude -p --output-format json``.

    That command wraps the answer in a result envelope
    (``{"type":"result","result":...,"structured_output":{...}}``); the
    schema-conforming content lives under ``structured_output``. Reading the
    envelope directly yields all-empty fields — the silent failure that left
    every weekly synthesis thin. Falls back to a bare schema object (older
    CLIs) or a JSON string in ``result``, and raises otherwise so the caller
    logs the failure and drops to heuristics instead of emitting empties.
    """
    if isinstance(raw, dict):
        structured = raw.get("structured_output")
        if isinstance(structured, dict) and structured:
            return structured
        if "summary" in raw or "completed_tasks" in raw:
            return raw
        result = raw.get("result")
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        if raw.get("is_error"):
            raise RuntimeError(
                f"claude summarizer returned an error envelope: {str(raw.get('result'))[:200]}"
            )
    raise RuntimeError("claude summarizer returned no structured_output payload")


def call_claude_summarizer(
    root: Path,
    manifest: dict[str, Any],
    transcript_text: str,
    context_text: str,
) -> dict[str, Any]:
    real_claude = find_real_binary("claude", root)
    template = summary_prompt_template(root)
    prompt = template.format(
        manifest=json.dumps(manifest, indent=2, sort_keys=True),
        context=context_text,
        transcript=transcript_text[-40000:],
    )
    command = [
        real_claude,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(model_summary_schema()),
        prompt,
    ]
    emit_progress("summarizing", "calling Claude to summarize...")
    result = subprocess.run(
        command,
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = _extract_claude_schema_payload(json.loads(result.stdout))
    for key in PROMOTION_KEYS + (
        "completed_tasks",
        "decisions",
        "open_questions",
        "follow_ups",
        "signals",
        "health_signals",
        "what_mattered",
        "repeated_patterns",
        "model_updates",
        "easier_next_time",
    ):
        payload[key] = dedupe_keep_order(payload.get(key, []))
    return payload


def codex_error_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = strip_ansi((exc.stderr or "").strip())
        stdout = strip_ansi((exc.stdout or "").strip())
        detail = stderr or stdout
        if detail:
            return f"{exc}. Detail: {detail.splitlines()[-1]}"
    return str(exc)


def call_codex_structured(
    root: Path,
    prompt: str,
    schema: dict[str, Any],
    *,
    phase: str,
    action: str,
    timeout: int = 120,
) -> dict[str, Any]:
    real_codex = find_real_binary("codex", root)
    with tempfile.TemporaryDirectory(prefix="exocortex-codex-") as temp_dir:
        temp_root = Path(temp_dir)
        schema_path = temp_root / "schema.json"
        output_path = temp_root / "output.json"
        schema_path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
        command = [
            real_codex,
            "exec",
            "-C",
            str(root),
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            prompt,
        ]
        emit_progress(phase, f"calling Codex to {action}...")
        result = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        payload_text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
        if not payload_text:
            payload_text = result.stdout.strip()
        if not payload_text:
            raise RuntimeError(f"Codex returned no structured output for {action}.")
        return json.loads(payload_text)


def call_codex_summarizer(
    root: Path,
    manifest: dict[str, Any],
    transcript_text: str,
    context_text: str,
) -> dict[str, Any]:
    template = summary_prompt_template(root)
    prompt = template.format(
        manifest=json.dumps(manifest, indent=2, sort_keys=True),
        context=context_text,
        transcript=transcript_text[-40000:],
    )
    payload = call_codex_structured(
        root,
        prompt,
        model_summary_schema(),
        phase="summarizing",
        action="summarize the session",
    )
    for key in PROMOTION_KEYS + (
        "completed_tasks",
        "decisions",
        "open_questions",
        "follow_ups",
        "signals",
        "health_signals",
        "what_mattered",
        "repeated_patterns",
        "model_updates",
        "easier_next_time",
    ):
        payload[key] = dedupe_keep_order(payload.get(key, []))
    return payload


def ensure_summary_defaults(
    manifest: dict[str, Any],
    transcript_entries: list[dict[str, str]],
    data: dict[str, Any],
) -> dict[str, Any]:
    # Model-backed output is authoritative for candidates. Heuristic is only
    # used to backfill narrative/context fields the model may have omitted
    # (summary string, health signals). Never union regex candidates with
    # model candidates — that poisons the durable layer with CLI help text
    # and shell noise the model was explicitly told to skip.
    heuristic = heuristic_summary_data(manifest, transcript_entries)
    narrative_backfill_keys = {"summary", "rationale", "confidence"}
    merged = dict(data)
    for key, hval in heuristic.items():
        if key in narrative_backfill_keys:
            if not merged.get(key):
                merged[key] = hval
        elif key not in merged:
            merged[key] = hval
    for key in PROMOTION_KEYS + (
        "completed_tasks",
        "decisions",
        "open_questions",
        "follow_ups",
        "signals",
        "health_signals",
        "what_mattered",
        "repeated_patterns",
        "model_updates",
        "easier_next_time",
    ):
        merged[key] = dedupe_keep_order(merged.get(key, []))
    return merged


def summarize_session(
    root: Path,
    manifest: dict[str, Any],
    transcript_text: str,
    transcript_entries: list[dict[str, str]],
    context_text: str,
) -> dict[str, Any]:
    provider = os.environ.get("EXOCORTEX_SUMMARIZER_PROVIDER", "claude").strip().lower()
    failures: list[str] = []
    if provider in {"claude", "codex"}:
        providers = [provider]
        if provider == "claude":
            providers.append("codex")
        for candidate in providers:
            try:
                if candidate == "claude":
                    payload = call_claude_summarizer(root, manifest, transcript_text, context_text)
                    payload["rationale"] = (
                        payload.get("rationale", "").strip()
                        or "Model-backed semantic extraction via Claude print mode."
                    )
                    payload["candidate_source"] = MODEL_CANDIDATE_SOURCE
                else:
                    payload = call_codex_summarizer(root, manifest, transcript_text, context_text)
                    default_rationale = "Model-backed semantic extraction via Codex exec."
                    if failures:
                        default_rationale = (
                            "Claude summarizer failed after the session closed; Codex completed the summary."
                        )
                    payload["rationale"] = payload.get("rationale", "").strip() or default_rationale
                    if failures:
                        payload["rationale"] += f" Claude failure: {failures[-1]}"
                    payload["candidate_source"] = MODEL_CANDIDATE_SOURCE
                return ensure_summary_defaults(manifest, transcript_entries, payload)
            except Exception as exc:  # pragma: no cover - fallback path
                failures.append(f"{candidate}: {codex_error_detail(exc)}")
    if failures:
        fallback = clear_candidate_lists(heuristic_summary_data(manifest, transcript_entries))
        fallback["confidence"] = "low"
        fallback["rationale"] = (
            "Model-backed summarization failed. Summary fell back to heuristics, but promotion candidates "
            f"were suppressed. Failures: {' | '.join(failures)}"
        )
        log_synthesis_error(root, manifest, failures)
        return fallback
    fallback = clear_candidate_lists(heuristic_summary_data(manifest, transcript_entries))
    fallback["confidence"] = "low"
    fallback["rationale"] = (
        "No model-backed summarizer provider was configured. Summary used heuristics, but promotion "
        "candidates were suppressed."
    )
    return fallback


def context_default_path(manifest: dict[str, Any], filename: str) -> str:
    if manifest.get("project") and manifest.get("domain"):
        return f"domains/{manifest['domain']}/projects/{manifest['project']}/{filename}"
    if manifest.get("domain"):
        return f"domains/{manifest['domain']}/{filename}"
    return filename


def self_model_layer(text: str) -> str:
    if re.search(r"\b(today|right now|currently|recently|lately|this session|this week|blocked|stressed|tired)\b", text, re.I):
        return "dynamic"
    return "stable"


def suggested_destination(manifest: dict[str, Any], candidate_type: str, text: str) -> str:
    if candidate_type == "memory":
        return context_default_path(manifest, "MEMORY.md")
    if candidate_type == "workflow":
        return context_default_path(manifest, "WORKFLOWS.md")
    if candidate_type == "decision_rule":
        return context_default_path(manifest, "DECISION RULES.md")
    if candidate_type == "skill":
        return context_default_path(manifest, "SKILLS.md")
    if candidate_type == "intent":
        return "system/OPEN LOOPS.md"
    if candidate_type == "self_model":
        return "system/SELF MODEL.md" if self_model_layer(text) == "stable" else context_default_path(manifest, "STATE.md")
    if candidate_type == "persona":
        return "system/PERSONA CALIBRATION.md"
    if candidate_type == "question_template":
        return "system/QUESTIONING.md"
    return "journal/inbox/pending-questions.md"


def artifact_kind(candidate_type: str, text: str) -> str:
    if candidate_type == "memory":
        return "memory_note"
    if candidate_type == "workflow":
        return "workflow"
    if candidate_type == "decision_rule":
        return "decision_rule"
    if candidate_type == "skill":
        return "skill"
    if candidate_type == "intent":
        return "open_loop"
    if candidate_type == "self_model":
        return "dynamic_state_note" if self_model_layer(text) == "dynamic" else "self_model_note"
    if candidate_type == "persona":
        return "persona_calibration"
    if candidate_type == "question_template":
        return "question_template"
    return "open_loop"


def why_it_matters(candidate_type: str) -> str:
    mapping = {
        "memory": "Likely durable preference, value, or standing constraint that can improve future context.",
        "workflow": "Potential reusable procedure that could make similar sessions easier next time.",
        "decision_rule": "Potential default or heuristic that can reduce repeated decision overhead.",
        "skill": "Potential reusable capability or artifact worth making explicit.",
        "intent": "Potential inferred future goal, automation, or commitment that may deserve confirmation or tracking.",
        "self_model": "Potential repeated pattern about how the user thinks, works, or gets blocked.",
        "persona": "Potential interaction-style calibration that can make future support more effective and natural.",
        "question_template": "Potential reusable question that may unlock clarity in future sessions.",
        "open_question": "Active unresolved question that may deserve follow-up or later synthesis.",
    }
    return mapping[candidate_type]


def build_candidate_records(manifest: dict[str, Any], data: dict[str, Any]) -> list[dict[str, Any]]:
    if data.get("candidate_source") == NO_CANDIDATE_SOURCE:
        return []
    source_time = manifest.get("ended_at") or manifest.get("started_at") or now_iso()
    category_map = {
        "memory": data.get("memory_candidates", []),
        "workflow": data.get("workflow_candidates", []),
        "decision_rule": data.get("decision_rule_candidates", []),
        "skill": data.get("skill_candidates", []),
        "intent": data.get("intent_candidates", []),
        "self_model": data.get("self_model_candidates", []),
        "persona": data.get("persona_candidates", []),
        "question_template": data.get("question_template_candidates", []),
        "open_question": data.get("open_questions", []),
    }
    records: list[dict[str, Any]] = []
    for candidate_type, items in category_map.items():
        for text in dedupe_keep_order(items):
            if is_candidate_text_noise(text):
                continue
            normalized = normalize_candidate_text(text)
            if not normalized:
                continue
            records.append(
                {
                    "candidate_type": candidate_type,
                    "text": text,
                    "normalized_key": normalized,
                    "signal_ladder": "candidate",
                    "evidence_count": 1,
                    "first_seen": source_time,
                    "last_seen": source_time,
                    "confidence": data.get("confidence", "low"),
                    "why_it_matters": why_it_matters(candidate_type),
                    "justification": why_it_matters(candidate_type),
                    "suggested_destination": suggested_destination(manifest, candidate_type, text),
                    "artifact_kind": artifact_kind(candidate_type, text),
                    "status": "pending",
                    "source_session_ids": [manifest["session_id"]],
                    "source": {
                        "session_id": manifest["session_id"],
                        "excerpt": text,
                    },
                    "source_excerpt": text,
                    "tier": "queue",
                    "contradicts": [],
                    "related_focus": [],
                    "domain": manifest.get("domain"),
                    "project": manifest.get("project"),
                    "level": manifest.get("level"),
                    "self_model_layer": self_model_layer(text) if candidate_type == "self_model" else None,
                    "intent_stage": "candidate" if candidate_type == "intent" else None,
                }
            )
    return records


def build_summary(
    manifest: dict[str, Any],
    transcript_entries: list[dict[str, str]],
    data: dict[str, Any],
) -> str:
    excerpt = [format_entry(entry) for entry in transcript_entries[:5]]
    minutes = duration_minutes(manifest.get("started_at"), manifest.get("ended_at"))
    lines = [
        "# Session Summary",
        "",
        f"- session_id: `{manifest['session_id']}`",
        f"- tool: `{manifest['tool']}`",
        f"- active_agent: `{manifest['active_agent']}`",
        f"- active_mode: `{manifest['active_mode']}`",
        f"- level: `{manifest['level']}`",
    ]
    if manifest.get("domain"):
        lines.append(f"- domain: `{manifest['domain']}`")
    if manifest.get("project"):
        lines.append(f"- project: `{manifest['project']}`")
    lines.extend(
        [
            f"- started_at: `{manifest.get('started_at')}`",
            f"- ended_at: `{manifest.get('ended_at')}`",
            f"- duration_minutes: `{minutes}`",
            f"- exit_code: `{manifest.get('exit_code')}`",
            f"- confidence: `{data['confidence']}`",
            "",
            "## Summary",
            "",
            data["summary"],
            "",
        ]
    )
    lines.extend(bullet_section("## Completed Tasks", data["completed_tasks"]))
    lines.extend(bullet_section("## Decisions", data["decisions"]))
    lines.extend(bullet_section("## Open Questions", data["open_questions"]))
    lines.extend(bullet_section("## Follow-ups", data["follow_ups"]))
    lines.extend(bullet_section("## What Mattered", data.get("what_mattered", [])))
    lines.extend(bullet_section("## Repeated Patterns", data.get("repeated_patterns", [])))
    lines.extend(bullet_section("## Model Updates", data.get("model_updates", [])))
    lines.extend(bullet_section("## Easier Next Time", data.get("easier_next_time", [])))
    lines.extend(bullet_section("## Health Signals", data.get("health_signals", [])))
    lines.extend(bullet_section("## Confidence", [data["confidence"]]))
    lines.extend(bullet_section("## Rationale", [data["rationale"]]))
    lines.extend(
        [
            "## Timeline",
            "",
            f"- started_at: `{manifest.get('started_at')}`",
            f"- ended_at: `{manifest.get('ended_at')}`",
            "",
        ]
    )
    lines.extend(bullet_section("## Initial Signals", data["signals"]))
    lines.extend(["## Transcript Excerpt", ""])
    if excerpt:
        lines.extend(f"- {line}" for line in excerpt)
    else:
        lines.append("- No session content captured.")
    lines.append("")
    return "\n".join(lines)


def candidate_record_block(record: dict[str, Any]) -> list[str]:
    lines = [
        f"### {record['text']}",
        "",
        f"- candidate_type: `{record['candidate_type']}`",
        f"- signal_ladder: `{record['signal_ladder']}`",
        f"- evidence_count: `{record['evidence_count']}`",
        f"- confidence: `{record['confidence']}`",
        f"- suggested_destination: `{record['suggested_destination']}`",
        f"- artifact_kind: `{record['artifact_kind']}`",
        f"- why_it_matters: {record['why_it_matters']}",
        f"- justification: {record.get('justification') or record['why_it_matters']}",
        f"- tier: `{record.get('tier', 'queue')}`",
    ]
    if record.get("self_model_layer"):
        lines.append(f"- self_model_layer: `{record['self_model_layer']}`")
    if record.get("intent_stage"):
        lines.append(f"- intent_stage: `{record['intent_stage']}`")
    if record.get("domain"):
        lines.append(f"- domain: `{record['domain']}`")
    if record.get("project"):
        lines.append(f"- project: `{record['project']}`")
    lines.extend(
        [
            f"- first_seen: `{record['first_seen']}`",
            f"- last_seen: `{record['last_seen']}`",
            "",
        ]
    )
    return lines


def build_candidates(manifest: dict[str, Any], data: dict[str, Any], candidate_records: list[dict[str, Any]]) -> str:
    lines = [
        "# Promotion Candidates",
        "",
        f"- session_id: `{manifest['session_id']}`",
        f"- generated_at: `{manifest.get('ended_at') or manifest.get('started_at')}`",
        f"- confidence: `{data['confidence']}`",
        "",
    ]
    lines.extend(bullet_section("## What Should Be Easier Next Time", data.get("easier_next_time", [])))
    lines.extend(bullet_section("## Memory Candidates", data["memory_candidates"]))
    lines.extend(bullet_section("## Workflow Candidates", data["workflow_candidates"]))
    lines.extend(bullet_section("## Skill Candidates", data["skill_candidates"]))
    lines.extend(bullet_section("## Decision Rule Candidates", data["decision_rule_candidates"]))
    lines.extend(bullet_section("## Intent Candidates", data.get("intent_candidates", [])))
    lines.extend(bullet_section("## Self Model Candidates", data["self_model_candidates"]))
    lines.extend(bullet_section("## Persona Candidates", data.get("persona_candidates", [])))
    lines.extend(bullet_section("## Question Template Candidates", data["question_template_candidates"]))
    lines.extend(bullet_section("## Open Question Candidates", data["open_questions"]))
    lines.extend(bullet_section("## Rationale", [data["rationale"]]))
    lines.extend(["## Structured Candidate Records", ""])
    if candidate_records:
        for record in candidate_records:
            lines.extend(candidate_record_block(record))
    else:
        lines.append("- None extracted.")
        lines.append("")
    return "\n".join(lines)


def daily_raw_block(root: Path, manifest: dict[str, Any]) -> str:
    started = manifest.get("started_at", "")
    started_short = started[:19].replace("T", " ") if started else "unknown"
    context_path = manifest.get("context_path", "")
    transcript_path = manifest.get("transcript_path", "")
    transcript_file = root / transcript_path
    transcript_body = transcript_file.read_text(encoding="utf-8") if transcript_file.exists() else ""
    health_snapshot = manifest.get("health_snapshot", {}) or {}
    health_lines = ["### Health Snapshot", ""]
    if health_snapshot:
        health_lines.extend(f"- {key}: `{value}`" for key, value in health_snapshot.items())
    else:
        health_lines.append("- none recorded")
    health_lines.append("")
    return "\n".join(
        [
            f"## {started_short} {manifest['tool']} - {manifest['active_agent']}",
            "",
            f"- session_id: `{manifest['session_id']}`",
            f"- started_at: `{manifest.get('started_at')}`",
            f"- ended_at: `{manifest.get('ended_at')}`",
            f"- level: `{manifest['level']}`",
            f"- cwd: `{manifest.get('cwd')}`",
            f"- exit_code: `{manifest.get('exit_code')}`",
            f"- transcript: `{transcript_path}`",
            f"- context: `{context_path}`",
            "",
            *health_lines,
            "### Captured Session Stream",
            "",
            transcript_body.rstrip(),
            "",
        ]
    )


def daily_summary_block(manifest: dict[str, Any], data: dict[str, Any]) -> str:
    started = manifest.get("started_at", "")
    started_short = started[:19].replace("T", " ") if started else "unknown"
    block = [
        f"## {started_short} {manifest['tool']} - {manifest['active_agent']}",
        "",
        f"- started_at: `{manifest.get('started_at')}`",
        f"- ended_at: `{manifest.get('ended_at')}`",
        f"- session_id: `{manifest['session_id']}`",
        f"- confidence: `{data['confidence']}`",
        "",
        "### Summary",
        "",
        data["summary"],
        "",
    ]
    block.extend(bullet_section("### Completed Tasks", data["completed_tasks"]))
    block.extend(bullet_section("### Decisions", data["decisions"]))
    block.extend(bullet_section("### Open Questions", data["open_questions"]))
    block.extend(bullet_section("### Follow-ups", data["follow_ups"]))
    block.extend(bullet_section("### What Mattered", data.get("what_mattered", [])))
    block.extend(bullet_section("### Repeated Patterns", data.get("repeated_patterns", [])))
    block.extend(bullet_section("### Model Updates", data.get("model_updates", [])))
    block.extend(bullet_section("### Easier Next Time", data.get("easier_next_time", [])))
    block.extend(bullet_section("### Health Signals", data.get("health_signals", [])))
    block.extend(bullet_section("### Confidence", [data["confidence"]]))
    block.extend(bullet_section("### Rationale", [data["rationale"]]))
    block.extend(bullet_section("### Signals", data["signals"]))
    return "\n".join(block)


def weekly_id(iso_dt: str) -> str:
    dt = parse_iso(iso_dt)
    if not dt:
        return "unknown-week"
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def monthly_id(iso_dt: str) -> str:
    dt = parse_iso(iso_dt)
    if not dt:
        return "unknown-month"
    return f"{dt.year}-{dt.month:02d}"


def quarterly_id(iso_dt: str) -> str:
    dt = parse_iso(iso_dt)
    if not dt:
        return "unknown-quarter"
    return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"


# Period synthesis tuning. Weekly is rebuilt every session (live). Monthly is
# also rebuilt every session because it rolls up the weekly that just changed
# (stale-hours 0 => always). Quarterly rolls up months and barely shifts
# session-to-session, so it is throttled. Set any to 0 to force live rebuilds.
_PERIOD_INPUT_CAP = 40000
_MONTHLY_STALE_HOURS = 0.0
_QUARTERLY_STALE_HOURS = 24.0


def period_synthesis_stale(json_path: Path, max_age_hours: float) -> bool:
    """True when a period-synthesis ``.json`` sidecar is missing or older than
    ``max_age_hours``. Prefers the embedded ``generated_at`` (survives a
    cloned/rsynced tree where mtime resets); falls back to file mtime. A
    threshold <= 0 always rebuilds."""
    if max_age_hours <= 0 or not json_path.exists():
        return True
    generated = None
    try:
        generated = parse_iso(read_json(json_path).get("generated_at"))
    except Exception:
        generated = None
    if generated is None:
        try:
            generated = datetime.fromtimestamp(json_path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return True
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - generated).total_seconds() / 3600.0
    return age_hours >= max_age_hours


def signal_ladder(evidence_count: int) -> str:
    if evidence_count >= 4:
        return "trusted_durable_signal"
    if evidence_count >= 2:
        return "repeated_pattern"
    return "candidate"


def confidence_value(confidence: str) -> int:
    return CONFIDENCE_SCORES.get(confidence, 1)


def candidate_score(record: dict[str, Any], reference_time: datetime | None = None) -> float:
    reference = reference_time or datetime.now(timezone.utc)
    last_seen = parse_iso(record.get("last_seen"))
    recency_bonus = 0.0
    if last_seen:
        delta_days = max((reference - last_seen).total_seconds() / 86400.0, 0.0)
        recency_bonus = max(10.0 - min(delta_days, 10.0), 0.0)
    ladder_bonus = {"candidate": 0.0, "repeated_pattern": 8.0, "trusted_durable_signal": 15.0}[record["signal_ladder"]]
    return (record["evidence_count"] * 10.0) + ladder_bonus + (confidence_value(record["confidence"]) * 3.0) + recency_bonus


def load_candidate_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((root / "journal" / "sessions").glob("*/*.candidates.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        for record in payload.get("candidate_records", []):
            if is_candidate_text_noise(record.get("text", "")):
                continue
            records.append(record)
    return records


def aggregate_candidate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        key = (
            record["candidate_type"],
            record["normalized_key"],
            record["suggested_destination"],
        )
        bucket = buckets.get(key)
        session_ids = set(record.get("source_session_ids", []))
        if bucket is None:
            buckets[key] = {
                "candidate_type": record["candidate_type"],
                "text": record["text"],
                "normalized_key": record["normalized_key"],
                "suggested_destination": record["suggested_destination"],
                "artifact_kind": record["artifact_kind"],
                "why_it_matters": record["why_it_matters"],
                "justification": record.get("justification") or record["why_it_matters"],
                "confidence": record["confidence"],
                "first_seen": record["first_seen"],
                "last_seen": record["last_seen"],
                "evidence_count": len(session_ids) or 1,
                "signal_ladder": "candidate",
                "status": "pending",
                "tier": record.get("tier", "queue"),
                "contradicts": record.get("contradicts", []),
                "related_focus": record.get("related_focus", []),
                "source_session_ids": sorted(session_ids),
                "recent_evidence": [record["text"]],
                "domains": sorted({record.get("domain")} - {None}),
                "projects": sorted({record.get("project")} - {None}),
                "self_model_layer": record.get("self_model_layer"),
            }
            continue

        bucket["confidence"] = max(
            (bucket["confidence"], record["confidence"]),
            key=confidence_value,
        )
        bucket["first_seen"] = min(bucket["first_seen"], record["first_seen"])
        bucket["last_seen"] = max(bucket["last_seen"], record["last_seen"])
        bucket["source_session_ids"] = sorted(set(bucket["source_session_ids"]) | session_ids)
        bucket["evidence_count"] = len(bucket["source_session_ids"]) or 1
        bucket["recent_evidence"] = dedupe_keep_order(bucket["recent_evidence"] + [record["text"]])[-3:]
        bucket["contradicts"] = sorted(set(bucket.get("contradicts", [])) | set(record.get("contradicts", [])))
        bucket["related_focus"] = sorted(set(bucket.get("related_focus", [])) | set(record.get("related_focus", [])))
        bucket["domains"] = sorted(set(bucket["domains"]) | ({record.get("domain")} - {None}))
        bucket["projects"] = sorted(set(bucket["projects"]) | ({record.get("project")} - {None}))
        if not bucket.get("self_model_layer") and record.get("self_model_layer"):
            bucket["self_model_layer"] = record["self_model_layer"]

    aggregated = list(buckets.values())
    for record in aggregated:
        record["signal_ladder"] = signal_ladder(record["evidence_count"])
        record["score"] = round(candidate_score(record), 2)
    aggregated.sort(
        key=lambda item: (
            {"trusted_durable_signal": 3, "repeated_pattern": 2, "candidate": 1}[item["signal_ladder"]],
            item["evidence_count"],
            confidence_value(item["confidence"]),
            item["last_seen"],
        ),
        reverse=True,
    )
    return aggregated


def render_queue_section(entries: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    if not entries:
        lines.extend(["- None queued.", ""])
        return lines
    for entry in entries:
        lines.extend(
            [
                f"### {entry['text']}",
                "",
                f"- signal_ladder: `{entry['signal_ladder']}`",
                f"- evidence_count: `{entry['evidence_count']}`",
                f"- confidence: `{entry['confidence']}`",
                f"- suggested_destination: `{entry['suggested_destination']}`",
                f"- artifact_kind: `{entry['artifact_kind']}`",
                f"- why_it_matters: {entry['why_it_matters']}",
                f"- why_promoted: {entry.get('justification') or entry['why_it_matters']}",
                f"- priority: `{entry.get('tier', 'queue')}`",
                f"- first_seen: `{entry['first_seen']}`",
                f"- last_seen: `{entry['last_seen']}`",
            ]
        )
        if entry.get("intent_stage"):
            lines.append(f"- intent_stage: `{entry['intent_stage']}`")
        if entry.get("review_recommendation"):
            lines.append(f"- review_recommendation: `{entry['review_recommendation']}`")
        if entry.get("commitment_strength"):
            lines.append(f"- commitment_strength: `{entry['commitment_strength']}`")
        if entry.get("domains"):
            lines.append(f"- domains: `{', '.join(entry['domains'])}`")
        if entry.get("projects"):
            lines.append(f"- projects: `{', '.join(entry['projects'])}`")
        if entry.get("self_model_layer"):
            lines.append(f"- self_model_layer: `{entry['self_model_layer']}`")
        if entry.get("source_session_ids"):
            lines.append(f"- source: sessions `{', '.join(entry['source_session_ids'])}`")
        lines.append("- candidate_content: |")
        for content_line in entry["text"].splitlines() or [""]:
            lines.append(f"    {content_line}")
        if entry.get("promoted_to"):
            lines.append(f"- promoted_to: `{entry['promoted_to']}`")
        if entry.get("reviewed_at"):
            lines.append(f"- reviewed_at: `{entry['reviewed_at']}`")
        if entry.get("review_note"):
            lines.append(f"- review_note: {entry['review_note']}")
        lines.append("- recent_evidence:")
        lines.extend(f"  - {item}" for item in entry.get("recent_evidence", []))
        lines.append("")
    return lines


def queue_bucket(entries: list[dict[str, Any]], candidate_type: str) -> list[dict[str, Any]]:
    filtered = [entry for entry in entries if entry["candidate_type"] == candidate_type]
    filtered.sort(key=lambda item: (item["score"], item["last_seen"]), reverse=True)
    return filtered


def intent_review_sections(entries: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    ready = [
        item
        for item in entries
        if item.get("review_recommendation") == "confirm_open_loop"
    ]
    watch = [
        item
        for item in entries
        if item.get("review_recommendation") == "keep_inferred"
    ]
    return [
        ("## Ready To Confirm As Open Loops", ready),
        ("## Keep As Inferred Intents", watch),
    ]


def write_review_queues(root: Path, aggregated: list[dict[str, Any]]) -> None:
    inbox = root / "journal" / "inbox"
    annotated = intent_review.annotate_records(aggregated, root)
    queue_specs = [
        ("pending-memory.md", QUEUE_TITLES["memory"], [("## Memory", queue_bucket(annotated, "memory"))]),
        ("pending-workflows.md", QUEUE_TITLES["workflow"], [("## Workflows", queue_bucket(annotated, "workflow"))]),
        ("pending-rules.md", QUEUE_TITLES["decision_rule"], [("## Decision Rules", queue_bucket(annotated, "decision_rule"))]),
        ("pending-skills.md", QUEUE_TITLES["skill"], [("## Skills", queue_bucket(annotated, "skill"))]),
        ("pending-self-model.md", QUEUE_TITLES["self_model"], [("## Self Model", queue_bucket(annotated, "self_model"))]),
        ("pending-persona.md", QUEUE_TITLES["persona"], [("## Persona Calibration", queue_bucket(annotated, "persona"))]),
        (
            "pending-questions.md",
            QUEUE_TITLES["question"],
            [
                ("## Question Templates", queue_bucket(annotated, "question_template")),
                ("## Open Questions", queue_bucket(annotated, "open_question")),
            ],
        ),
    ]
    for filename, title, sections in queue_specs:
        lines = [
            f"# {title}",
            "",
            "Signal ladder: `candidate` -> `repeated_pattern` -> `trusted_durable_signal`.",
            "",
        ]
        for heading, entries in sections:
            lines.append(heading)
            lines.append("")
            lines.extend(render_queue_section(entries))
        write_text(inbox / filename, "\n".join(lines).rstrip() + "\n")

    intent_lines = [
        f"# {QUEUE_TITLES['intent']}",
        "",
        "Promotion ladder: `candidate` -> `inferred_intent` -> `confirmed_open_loop` -> `priority`.",
        "",
        "## Review Rules",
        "",
        "- Keep items as `inferred_intent` when they are one-off or softly phrased future signals.",
        "- Confirm an item into `system/OPEN LOOPS.md` only after review when repetition, confidence, or commitment strength justifies it.",
        "- Promote an intent-originated item into `system/PRIORITIES.md` only after it has first been confirmed as an open loop and later proves urgent or repeated.",
        "- Rejected and already promoted items leave this pending queue but remain visible in `journal/inbox/reviewed-intents.md`.",
        "",
    ]
    for heading, entries in intent_review_sections(intent_review.pending_intents(annotated)):
        intent_lines.append(heading)
        intent_lines.append("")
        intent_lines.extend(render_queue_section(entries))
    write_text(inbox / "pending-intents.md", "\n".join(intent_lines).rstrip() + "\n")
    intent_review.write_reviewed_intents(root, intent_review.reviewed_intents(annotated))

    review_lines = [
        "# Review Queue",
        "",
        "Grouped promotion queue generated from structured session candidates.",
        "",
    ]
    for heading, candidate_type in (
        ("## Memory", "memory"),
        ("## Workflows", "workflow"),
        ("## Decision Rules", "decision_rule"),
        ("## Skills", "skill"),
        ("## Self Model", "self_model"),
        ("## Persona Calibration", "persona"),
        ("## Question Templates", "question_template"),
        ("## Open Questions", "open_question"),
    ):
        review_lines.append(heading)
        review_lines.append("")
        review_lines.extend(render_queue_section(queue_bucket(annotated, candidate_type)))
    review_lines.append("## Intents Ready To Confirm")
    review_lines.append("")
    review_lines.extend(
        render_queue_section(
            [item for item in intent_review.pending_intents(annotated) if item.get("review_recommendation") == "confirm_open_loop"]
        )
    )
    review_lines.append("## Intents To Watch")
    review_lines.append("")
    review_lines.extend(
        render_queue_section(
            [item for item in intent_review.pending_intents(annotated) if item.get("review_recommendation") == "keep_inferred"]
        )
    )
    write_text(inbox / "review-queue.md", "\n".join(review_lines).rstrip() + "\n")


def build_context_cache(aggregated: list[dict[str, Any]]) -> dict[str, Any]:
    def simplified(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidate_type": entry["candidate_type"],
            "text": entry["text"],
            "signal_ladder": entry["signal_ladder"],
            "evidence_count": entry["evidence_count"],
            "confidence": entry["confidence"],
            "score": entry["score"],
            "suggested_destination": entry["suggested_destination"],
            "why_it_matters": entry["why_it_matters"],
            "domains": entry.get("domains", []),
            "projects": entry.get("projects", []),
            "self_model_layer": entry.get("self_model_layer"),
            "last_seen": entry["last_seen"],
        }

    cache: dict[str, Any] = {
        "generated_at": now_iso(),
        "global": [],
        "by_domain": {},
        "by_project": {},
    }
    for entry in aggregated:
        if entry["candidate_type"] not in {"memory", "workflow", "decision_rule", "persona", "self_model"}:
            continue
        item = simplified(entry)
        cache["global"].append(item)
        for domain in entry.get("domains", []):
            cache["by_domain"].setdefault(domain, []).append(item)
        for project in entry.get("projects", []):
            domain = entry.get("domains", [None])[0]
            if domain and project:
                cache["by_project"].setdefault(f"{domain}/{project}", []).append(item)
    for key in ("global",):
        cache[key] = sorted(cache[key], key=lambda item: item["score"], reverse=True)[:20]
    for mapping_name in ("by_domain", "by_project"):
        for bucket, items in cache[mapping_name].items():
            cache[mapping_name][bucket] = sorted(items, key=lambda item: item["score"], reverse=True)[:20]
    return cache


STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "before",
    "being",
    "build",
    "for",
    "from",
    "have",
    "into",
    "that",
    "the",
    "this",
    "through",
    "with",
    "would",
    "your",
}


def content_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", text.lower())
        if token not in STOPWORDS
    }


def extract_current_focus(text: str) -> str:
    lines = text.splitlines()
    collected: list[str] = []
    in_focus = False
    for line in lines:
        if line.startswith("## "):
            if in_focus:
                break
            in_focus = line.strip().lower() == "## current focus"
            continue
        if in_focus:
            collected.append(line)
    return "\n".join(collected).strip()


def focus_paths_for_record(root: Path, record: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    domain = record.get("domain")
    project = record.get("project")
    if domain and project:
        paths.append(root / "domains" / domain / "projects" / project / "STATE.md")
    if domain:
        paths.append(root / "domains" / domain / "STATE.md")
    paths.append(root / "STATE.md")
    return paths


def record_related_focus(root: Path, record: dict[str, Any]) -> list[str]:
    candidate_tokens = content_tokens(record.get("text", ""))
    if not candidate_tokens:
        return []
    matches: list[str] = []
    for path in focus_paths_for_record(root, record):
        if not path.exists():
            continue
        try:
            focus = extract_current_focus(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not focus:
            continue
        overlap = candidate_tokens & content_tokens(focus)
        if len(overlap) >= 2:
            matches.append(str(path.relative_to(root)))
    return matches


def promotion_tier(root: Path, record: dict[str, Any]) -> str:
    """Classify a candidate as ``surface_now`` (bring to the human's attention
    now) or ``queue`` (stage for the review loop). It NEVER durably promotes.

    The auto-apply tier was removed:
    background may observe and stage only, never silently write to a durable
    file. Everything stages; the human decides in the review loop.
    """
    if record.get("contradicts"):
        return "surface_now"
    related_focus = record_related_focus(root, record)
    if related_focus and confidence_value(record.get("confidence", "low")) >= confidence_value("medium"):
        record["related_focus"] = related_focus
        return "surface_now"
    return "queue"


def route_current_promotions(
    root: Path,
    manifest: dict[str, Any],
    current_records: list[dict[str, Any]],
    aggregated: list[dict[str, Any]],
) -> None:
    by_key = {
        (record["candidate_type"], record["normalized_key"], record["suggested_destination"]): record
        for record in aggregated
    }
    surface_records: list[dict[str, Any]] = []
    for record in current_records:
        key = (record["candidate_type"], record["normalized_key"], record["suggested_destination"])
        aggregate = by_key.get(key, record)
        tier = promotion_tier(root, aggregate)
        record["tier"] = tier
        record["evidence_count"] = aggregate.get("evidence_count", record.get("evidence_count", 1))
        record["signal_ladder"] = aggregate.get("signal_ladder", record.get("signal_ladder", "candidate"))
        record["related_focus"] = aggregate.get("related_focus", [])
        aggregate["tier"] = tier
        if tier == "surface_now":
            surface_records.append(record)

    if not surface_records:
        return
    generated_at = manifest.get("ended_at") or manifest.get("started_at") or now_iso()
    lines = [
        f"## {generated_at} surface-now",
        "",
        f"- session_id: `{manifest.get('session_id')}`",
        f"- reason: candidate matched current focus or contradicted durable state",
        "",
    ]
    for record in surface_records:
        lines.extend(
            [
                f"### {record['text']}",
                "",
                f"- candidate_type: `{record['candidate_type']}`",
                f"- confidence: `{record['confidence']}`",
                f"- suggested_destination: `{record['suggested_destination']}`",
                f"- why_promoted: {record.get('justification') or record.get('why_it_matters')}",
                f"- related_focus: `{', '.join(record.get('related_focus', []))}`",
                "",
            ]
        )
    append_locked_once(
        root / "journal" / "inbox" / "surface-now.md",
        "\n".join(lines),
        manifest.get("session_id", "unknown"),
    )


def session_intelligence(manifest: dict[str, Any], data: dict[str, Any], candidate_records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "session_id": manifest["session_id"],
        "started_at": manifest.get("started_at"),
        "ended_at": manifest.get("ended_at"),
        "tool": manifest.get("tool"),
        "active_agent": manifest.get("active_agent"),
        "active_mode": manifest.get("active_mode"),
        "domain": manifest.get("domain"),
        "project": manifest.get("project"),
        "level": manifest.get("level"),
        "summary": data["summary"],
        "completed_tasks": data["completed_tasks"],
        "decisions": data["decisions"],
        "open_questions": data["open_questions"],
        "follow_ups": data["follow_ups"],
        "what_mattered": data.get("what_mattered", []),
        "repeated_patterns": data.get("repeated_patterns", []),
        "model_updates": data.get("model_updates", []),
        "easier_next_time": data.get("easier_next_time", []),
        "confidence": data["confidence"],
        "candidate_records": candidate_records,
    }


def load_weekly_intelligence(root: Path, week: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((root / "journal" / "sessions").glob("*/*.intelligence.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        if weekly_id(payload.get("started_at", "")) == week:
            records.append(payload)
    return records


def load_month_weeklies(root: Path, month: str) -> list[dict[str, Any]]:
    """Weekly synthesis JSON sidecars whose anchor falls in ``month`` (YYYY-MM)."""
    out: list[dict[str, Any]] = []
    for path in sorted((root / "journal" / "weekly").glob("*.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        if monthly_id(payload.get("anchor_date", "")) == month:
            out.append(payload)
    out.sort(key=lambda r: r.get("period_id", ""))
    return out


def load_quarter_monthlies(root: Path, quarter: str) -> list[dict[str, Any]]:
    """Monthly synthesis JSON sidecars whose anchor falls in ``quarter`` (YYYY-Qn)."""
    out: list[dict[str, Any]] = []
    for path in sorted((root / "journal" / "monthly").glob("*.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        if quarterly_id(payload.get("anchor_date", "")) == quarter:
            out.append(payload)
    out.sort(key=lambda r: r.get("period_id", ""))
    return out


def _assemble_week_input(records: list[dict[str, Any]]) -> str:
    """Flatten a week's session intelligence records into labeled text for the
    synthesizer. Tail-capped (keep most recent) to _PERIOD_INPUT_CAP."""
    blocks: list[str] = []
    for rec in records:
        header = (
            f"### Session {str(rec.get('session_id', '?'))[:8]} | "
            f"agent={rec.get('active_agent')} | mode={rec.get('active_mode')}"
        )
        project = rec.get("project") or rec.get("domain")
        if project:
            header += f" | project={project}"
        blocks.append(header)
        if rec.get("summary"):
            blocks.append(str(rec["summary"]).strip())
        for field, label in (
            ("decisions", "Decisions"),
            ("open_questions", "Open questions"),
            ("follow_ups", "Follow-ups"),
            ("what_mattered", "What mattered"),
            ("repeated_patterns", "Patterns"),
            ("model_updates", "Model updates"),
            ("easier_next_time", "Easier next time"),
        ):
            items = rec.get(field) or []
            if items:
                blocks.append(f"{label}: " + " | ".join(str(i) for i in items))
        blocks.append("")
    return "\n".join(blocks).strip()[-_PERIOD_INPUT_CAP:]


def _assemble_period_input(syntheses: list[dict[str, Any]]) -> str:
    """Flatten lower-level synthesis records (weeklies for a month, monthlies for
    a quarter) into labeled text for the next level up."""
    blocks: list[str] = []
    for syn in syntheses:
        blocks.append(f"### {syn.get('period_label', syn.get('period_id', '?'))}")
        if syn.get("narrative"):
            blocks.append(str(syn["narrative"]).strip())
        for field, header in PERIOD_AXES:
            items = syn.get(field) or []
            if items:
                blocks.append(f"{header.replace('## ', '')}: " + " | ".join(str(i) for i in items))
        blocks.append("")
    return "\n".join(blocks).strip()[-_PERIOD_INPUT_CAP:]


_PERIOD_LEVEL_INSTRUCTIONS = {
    "week": "This is a single week's sessions. Surface the recurring themes across the week; do not narrate each session in turn.",
    "month": "You are given this month's weekly syntheses. Abstract ACROSS the weeks: what persisted, escalated, or resolved. Do not concatenate the weeks.",
    "quarter": "You are given this quarter's monthly syntheses. Identify quarter-scale arcs and durable shifts in the work, the thinking, and the working relationship. Do not restate the months.",
}


def period_synthesis_prompt_template(root: Path) -> str:
    return (root / "tools" / "prompts" / "period_synthesis.md").read_text(encoding="utf-8")


def _format_period_prompt(root: Path, level: str, period_label: str, input_text: str) -> str:
    return period_synthesis_prompt_template(root).format(
        period_label=period_label,
        level=level,
        level_instruction=_PERIOD_LEVEL_INSTRUCTIONS.get(level, ""),
        rolled_up_input=input_text[-_PERIOD_INPUT_CAP:],
    )


def _dedupe_period_axes(payload: dict[str, Any]) -> dict[str, Any]:
    for field, _header in PERIOD_AXES:
        payload[field] = dedupe_keep_order([str(i) for i in (payload.get(field) or [])])
    return payload


def call_claude_period_synthesizer(root: Path, level: str, period_label: str, input_text: str) -> dict[str, Any]:
    real_claude = find_real_binary("claude", root)
    prompt = _format_period_prompt(root, level, period_label, input_text)
    command = [
        real_claude,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(period_synthesis_schema()),
        prompt,
    ]
    result = subprocess.run(command, cwd=str(root), capture_output=True, text=True, check=True)
    return _dedupe_period_axes(_extract_claude_schema_payload(json.loads(result.stdout)))


def call_codex_period_synthesizer(root: Path, level: str, period_label: str, input_text: str) -> dict[str, Any]:
    prompt = _format_period_prompt(root, level, period_label, input_text)
    payload = call_codex_structured(
        root,
        prompt,
        period_synthesis_schema(),
        phase="synthesizing",
        action=f"synthesize the {level}",
    )
    return _dedupe_period_axes(payload)


def log_period_error(root: Path, level: str, period_id: str, failures: list[str]) -> None:
    lines = [
        f"## {now_iso()} {level} synthesis failure",
        "",
        f"- period_id: `{period_id}`",
        "- effect: period synthesis skipped (kept any prior file)",
        "- failures:",
    ]
    lines.extend(f"  - {failure}" for failure in failures)
    lines.append("")
    path = root / "journal" / "inbox" / "synthesis-errors.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def render_period_synthesis(
    root: Path,
    level: str,
    period_id: str,
    period_label: str,
    anchor_date: str,
    input_text: str,
    *,
    source_count: int = 0,
    footer_lines: list[str] | None = None,
) -> dict[str, Any] | None:
    """Model-synthesize one period into the 4 axes and write both a `.md` and a
    machine-readable `.json` sidecar. Tries claude then codex; returns ``None``
    on total model failure (caller decides how to degrade). Never raises."""
    dirs = {"week": "weekly", "month": "monthly", "quarter": "quarterly"}
    out_dir = root / "journal" / dirs[level]
    out_dir.mkdir(parents=True, exist_ok=True)
    if not (out_dir / "README.md").exists():
        write_text(
            out_dir / "README.md",
            f"# {level.capitalize()} Synthesis\n\nModel-generated, axis-organized. One `{period_id_format(level)}` file per period (`.md` for reading, `.json` for rollup).\n",
        )

    provider = os.environ.get("EXOCORTEX_SUMMARIZER_PROVIDER", "claude").strip().lower()
    candidates = [provider, "codex"] if provider == "claude" else [provider]
    failures: list[str] = []
    payload: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            if candidate == "claude":
                payload = call_claude_period_synthesizer(root, level, period_label, input_text)
            elif candidate == "codex":
                payload = call_codex_period_synthesizer(root, level, period_label, input_text)
            else:
                continue
            break
        except Exception as exc:  # pragma: no cover - fallback path
            failures.append(f"{candidate}: {codex_error_detail(exc)}")
            payload = None
    if payload is None:
        log_period_error(root, level, period_id, failures)
        return None

    record: dict[str, Any] = {
        "level": level,
        "period_id": period_id,
        "period_label": period_label,
        "anchor_date": anchor_date,
        "generated_at": now_iso(),
        "source_count": source_count,
        "narrative": str(payload.get("narrative", "")).strip(),
        "confidence": payload.get("confidence", "low"),
    }
    for field, _header in PERIOD_AXES:
        record[field] = payload.get(field, [])
    write_json(out_dir / f"{period_id}.json", record)

    lines = [
        f"# {level.capitalize()} Synthesis - {period_id}",
        "",
        f"- generated_at: `{record['generated_at']}`",
        f"- sources: `{source_count}`",
        f"- confidence: `{record['confidence']}`",
        "",
    ]
    if record["narrative"]:
        lines.extend([record["narrative"], ""])
    for field, header in PERIOD_AXES:
        lines.extend(bullet_section(header, record[field]))
    if footer_lines:
        lines.extend(footer_lines)
    write_text(out_dir / f"{period_id}.md", "\n".join(lines))
    return record


def period_id_format(level: str) -> str:
    return {"week": "YYYY-Www", "month": "YYYY-MM", "quarter": "YYYY-Qn"}.get(level, "period")


def _weekly_footer(
    intelligence_records: list[dict[str, Any]], aggregated: list[dict[str, Any]]
) -> list[str]:
    """The mechanical operational footer kept on every weekly: promotion
    candidates ready for review + the sessions that fed the synthesis."""
    lines = ["## Promotion Candidates Ready For Review", ""]
    ready = [entry for entry in aggregated if entry["signal_ladder"] != "candidate"][:10]
    if ready:
        lines.extend(render_queue_section(ready))
    else:
        lines.extend(["- None yet.", ""])
    lines.extend(["## Sessions Included", ""])
    if intelligence_records:
        for record in intelligence_records:
            lines.append(
                f"- `{record['session_id']}` | agent=`{record.get('active_agent')}` | mode=`{record.get('active_mode')}` | confidence=`{record.get('confidence')}`"
            )
    else:
        lines.append("- None.")
    lines.append("")
    return lines


def render_weekly_synthesis(root: Path, week: str, intelligence_records: list[dict[str, Any]]) -> None:
    """Weekly = model-synthesized 4-axis body + mechanical operational footer.
    Falls back to the legacy mechanical body if the model synthesis fails, so
    the weekly file is always produced and never regresses to broken."""
    candidate_records: list[dict[str, Any]] = []
    for record in intelligence_records:
        candidate_records.extend(record.get("candidate_records", []))
    aggregated = aggregate_candidate_records(candidate_records)
    sessions_count = len(intelligence_records)
    footer = _weekly_footer(intelligence_records, aggregated)

    anchor = max((r.get("started_at", "") for r in intelligence_records), default="") or now_iso()
    result = render_period_synthesis(
        root,
        "week",
        week,
        f"Week {week}",
        anchor,
        _assemble_week_input(intelligence_records),
        source_count=sessions_count,
        footer_lines=footer,
    )
    if result is not None:
        return

    # Fallback: legacy mechanical body (model synthesis unavailable).
    weekly_dir = root / "journal" / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    open_questions = dedupe_keep_order(
        [item for record in intelligence_records for item in record.get("open_questions", [])]
    )[:8]
    easier_next_time = dedupe_keep_order(
        [item for record in intelligence_records for item in record.get("easier_next_time", [])]
    )[:8]
    model_updates = dedupe_keep_order(
        [item for record in intelligence_records for item in record.get("model_updates", [])]
    )[:8]
    repeated = [entry["text"] for entry in aggregated if entry["signal_ladder"] != "candidate"][:8]
    lines = [
        f"# Weekly Synthesis - {week}",
        "",
        f"- generated_at: `{now_iso()}`",
        f"- sessions_count: `{sessions_count}`",
        "",
    ]
    lines.extend(bullet_section("## Repeated High-Signal Patterns", repeated))
    lines.extend(bullet_section("## Model Updates", model_updates))
    lines.extend(bullet_section("## Open Questions", open_questions))
    lines.extend(bullet_section("## What Should Be Easier Next Week", easier_next_time))
    lines.extend(footer)
    write_text(weekly_dir / f"{week}.md", "\n".join(lines))


def _source_periods_footer(syntheses: list[dict[str, Any]]) -> list[str]:
    lines = ["## Source Periods", ""]
    for syn in syntheses:
        lines.append(
            f"- `{syn.get('period_id')}` | confidence=`{syn.get('confidence')}` | generated_at=`{syn.get('generated_at')}`"
        )
    lines.append("")
    return lines


def synthesize_higher_periods(root: Path, manifest: dict[str, Any]) -> None:
    """Roll the just-rebuilt weekly up into the month, and the month into the
    quarter, subject to staleness throttles. Each render swallows its own model
    failure; this is wrapped by the caller so postprocess never breaks."""
    started = manifest.get("started_at", "")
    anchor = started or now_iso()
    month = monthly_id(started)
    quarter = quarterly_id(started)

    monthly_json = root / "journal" / "monthly" / f"{month}.json"
    if period_synthesis_stale(monthly_json, _MONTHLY_STALE_HOURS):
        weeklies = load_month_weeklies(root, month)
        if weeklies:
            emit_progress("monthly", f"building monthly synthesis {month}")
            render_period_synthesis(
                root, "month", month, f"Month {month}", anchor,
                _assemble_period_input(weeklies),
                source_count=len(weeklies), footer_lines=_source_periods_footer(weeklies),
            )

    quarterly_json = root / "journal" / "quarterly" / f"{quarter}.json"
    if period_synthesis_stale(quarterly_json, _QUARTERLY_STALE_HOURS):
        monthlies = load_quarter_monthlies(root, quarter)
        if monthlies:
            emit_progress("quarterly", f"building quarterly synthesis {quarter}")
            render_period_synthesis(
                root, "quarter", quarter, f"Quarter {quarter}", anchor,
                _assemble_period_input(monthlies),
                source_count=len(monthlies), footer_lines=_source_periods_footer(monthlies),
            )


STATE_UPDATE_PROMPT = """\
You are updating a project STATE.md file immediately after a work session ended.

--- CURRENT STATE.md ---
{state}

--- SESSION DATA ---
Summary: {summary}

Completed tasks:
{completed}

Decisions made this session:
{decisions}

Open questions raised:
{open_questions}

Follow-ups / next steps:
{follow_ups}
--- END SESSION DATA ---

Update the STATE.md to reflect this session. Rules:
1. Mark any matching "[ ]" items under "## Next Steps" as "[x]" if the completed tasks clearly
   describe finishing that item. Only check off items you are confident were finished.
2. If the completed tasks or follow-ups change what the active focus should be, update
   "## Current Focus" to the most important remaining next step.
3. Add genuinely new decisions to the "## Decisions Made" table (preserve table format exactly).
4. Merge new open questions into "## Open Questions" — deduplicate against existing ones.
5. Do NOT invent information. Do NOT remove existing content. Do NOT reformat the file.
{output_instruction}
"""


def call_claude_state_updater(
    root: Path,
    manifest: dict[str, Any],
    state_path: Path,
    data: dict[str, Any],
) -> None:
    """Use Claude to surgically update a project STATE.md after a session."""
    real_claude = find_real_binary("claude", root)
    current_state = state_path.read_text(encoding="utf-8")

    def fmt_list(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "- none"

    prompt = STATE_UPDATE_PROMPT.format(
        state=current_state,
        summary=data.get("summary", "No summary available."),
        completed=fmt_list(data.get("completed_tasks", [])),
        decisions=fmt_list(data.get("decisions", [])),
        open_questions=fmt_list(data.get("open_questions", [])),
        follow_ups=fmt_list(data.get("follow_ups", [])),
        output_instruction="6. Return ONLY the updated STATE.md content — no preamble, no explanation, no code fences.",
    )
    result = subprocess.run(
        [real_claude, "-p", prompt],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
        timeout=90,
    )
    updated = result.stdout.strip()
    if updated and len(updated) > 100:  # sanity-check: non-trivial output
        state_path.write_text(updated + "\n", encoding="utf-8")
        emit_progress("state", f"STATE.md updated for {manifest.get('project')}")


def codex_state_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "state_md": {"type": "string"},
        },
        "required": ["state_md"],
        "additionalProperties": False,
    }


def call_codex_state_updater(
    root: Path,
    manifest: dict[str, Any],
    state_path: Path,
    data: dict[str, Any],
) -> None:
    current_state = state_path.read_text(encoding="utf-8")

    def fmt_list(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "- none"

    prompt = STATE_UPDATE_PROMPT.format(
        state=current_state,
        summary=data.get("summary", "No summary available."),
        completed=fmt_list(data.get("completed_tasks", [])),
        decisions=fmt_list(data.get("decisions", [])),
        open_questions=fmt_list(data.get("open_questions", [])),
        follow_ups=fmt_list(data.get("follow_ups", [])),
        output_instruction=(
            "6. Return JSON matching the output schema. Put the full updated STATE.md content "
            "in `state_md` with no markdown fences or commentary outside that field."
        ),
    )
    payload = call_codex_structured(
        root,
        prompt,
        codex_state_schema(),
        phase="state",
        action="update STATE.md",
        timeout=180,
    )
    updated = payload.get("state_md", "").strip()
    if updated and len(updated) > 100:
        state_path.write_text(updated + "\n", encoding="utf-8")
        emit_progress("state", f"STATE.md updated for {manifest.get('project')}")


def heuristic_state_append(
    state_path: Path,
    manifest: dict[str, Any],
    data: dict[str, Any],
) -> None:
    """Fallback: append a brief session-note block to STATE.md without touching existing content."""
    session_id = manifest.get("session_id", "unknown")
    ended = (manifest.get("ended_at") or "")[:19].replace("T", " ")
    lines = [
        "",
        f"<!-- session:{session_id} ended:{ended} -->",
    ]
    if data.get("completed_tasks"):
        lines.append("\n## Recent Completions (auto-appended)")
        lines.extend(f"- {t}" for t in data["completed_tasks"][:5])
    if data.get("open_questions"):
        lines.append("\n## New Open Questions (auto-appended)")
        lines.extend(f"- {q}" for q in data["open_questions"][:5])
    block = "\n".join(lines) + "\n"
    with state_path.open("a", encoding="utf-8") as fh:
        fh.write(block)


def update_project_state(
    root: Path,
    manifest: dict[str, Any],
    data: dict[str, Any],
    provider: str,
) -> None:
    """Update the project STATE.md if this session belongs to a project."""
    domain = manifest.get("domain")
    project = manifest.get("project")
    if not domain or not project:
        return
    state_path = root / "domains" / domain / "projects" / project / "STATE.md"
    if not state_path.exists():
        return
    emit_progress("state", f"updating STATE.md for {domain}/{project}")
    if provider in {"claude", "codex"}:
        updaters: list[tuple[str, Any]] = []
        if provider == "claude":
            updaters.append(("Claude", call_claude_state_updater))
            updaters.append(("Codex", call_codex_state_updater))
        else:
            updaters.append(("Codex", call_codex_state_updater))
        for label, updater in updaters:
            try:
                updater(root, manifest, state_path, data)
                return
            except Exception as exc:
                print(
                    f"[process_session] STATE.md {label} update failed: {codex_error_detail(exc)}",
                    file=sys.stderr,
                )
    # Heuristic fallback: safe append-only
    try:
        heuristic_state_append(state_path, manifest, data)
    except Exception as exc:
        print(f"[process_session] STATE.md heuristic append failed: {exc}", file=sys.stderr)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: process_session.py <manifest_path>", file=sys.stderr)
        return 2

    manifest_path = Path(sys.argv[1]).resolve()
    manifest = read_json(manifest_path)
    root = Path(manifest["root"]).resolve()
    transcript_path = root / manifest["transcript_path"]
    summary_path = root / manifest["summary_path"]
    candidates_path = root / manifest["candidates_path"]
    context_path = root / manifest["context_path"]

    candidates_json_path = candidates_path.with_suffix(".json")
    intelligence_path = summary_path.with_suffix(".intelligence.json")
    manifest["candidates_json_path"] = str(candidates_json_path.relative_to(root))
    manifest["intelligence_path"] = str(intelligence_path.relative_to(root))
    write_json(manifest_path, manifest)

    transcript_text, transcript_entries = load_session_transcript(root, manifest)
    context_text = context_path.read_text(encoding="utf-8") if context_path.exists() else ""

    data = summarize_session(root, manifest, transcript_text, transcript_entries, context_text)
    candidate_records = build_candidate_records(manifest, data)
    historical_records = [
        record
        for record in load_candidate_records(root)
        if manifest["session_id"] not in record.get("source_session_ids", [])
    ]
    aggregated = aggregate_candidate_records(historical_records + candidate_records)
    route_current_promotions(root, manifest, candidate_records, aggregated)

    emit_progress("writing", "writing summary and candidates")
    summary_path.write_text(build_summary(manifest, transcript_entries, data), encoding="utf-8")
    candidates_path.write_text(build_candidates(manifest, data, candidate_records), encoding="utf-8")
    write_json(
        candidates_json_path,
        {
            "session_id": manifest["session_id"],
            "generated_at": manifest.get("ended_at") or manifest.get("started_at") or now_iso(),
            "confidence": data["confidence"],
            "candidate_records": candidate_records,
        },
    )
    write_json(intelligence_path, session_intelligence(manifest, data, candidate_records))

    date_str = manifest["started_at"][:10]
    raw_daily = root / "journal" / "raw" / f"{date_str}.md"
    summarised_daily = root / "journal" / "summarised" / f"{date_str}.md"

    emit_progress("journaling", "updating daily journal")
    append_locked_once(raw_daily, daily_raw_block(root, manifest), manifest["session_id"])
    append_locked_once(summarised_daily, daily_summary_block(manifest, data), manifest["session_id"])

    write_review_queues(root, aggregated)
    write_json(root / "journal" / "inbox" / "context-cache.json", build_context_cache(aggregated))
    emit_progress("weekly", "building weekly synthesis")
    week = weekly_id(manifest["started_at"])
    render_weekly_synthesis(root, week, load_weekly_intelligence(root, week))
    try:
        synthesize_higher_periods(root, manifest)
    except Exception as exc:  # pragma: no cover - postprocess must never break
        log_period_error(root, "rollup", "monthly+quarterly", [str(exc)])

    provider = os.environ.get("EXOCORTEX_SUMMARIZER_PROVIDER", "claude").strip().lower()
    update_project_state(root, manifest, data, provider)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
