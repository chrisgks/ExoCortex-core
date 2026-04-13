#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
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
    result = subprocess.run(
        command,
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
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
    heuristic = heuristic_summary_data(manifest, transcript_entries)
    merged = dict(heuristic)
    for key, value in data.items():
        if isinstance(value, list):
            merged[key] = merge_unique(value, heuristic.get(key, []), limit=max(len(value), len(heuristic.get(key, [])), 5))
        elif value not in (None, ""):
            merged[key] = value
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
    provider = os.environ.get("EXOCORTEX_SUMMARIZER_PROVIDER", "heuristic").strip().lower()
    if provider == "claude":
        try:
            payload = call_claude_summarizer(root, manifest, transcript_text, context_text)
            payload["rationale"] = (
                payload.get("rationale", "").strip()
                or "Model-backed semantic extraction via Claude print mode."
            )
            return ensure_summary_defaults(manifest, transcript_entries, payload)
        except Exception as exc:  # pragma: no cover - fallback path
            fallback = heuristic_summary_data(manifest, transcript_entries)
            fallback["confidence"] = "low"
            fallback["rationale"] = (
                "Claude summarizer failed and the worker fell back to heuristics. "
                f"Failure: {exc}"
            )
            return fallback
    return heuristic_summary_data(manifest, transcript_entries)


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
                    "suggested_destination": suggested_destination(manifest, candidate_type, text),
                    "artifact_kind": artifact_kind(candidate_type, text),
                    "status": "pending",
                    "source_session_ids": [manifest["session_id"]],
                    "source_excerpt": text,
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
                "confidence": record["confidence"],
                "first_seen": record["first_seen"],
                "last_seen": record["last_seen"],
                "evidence_count": len(session_ids) or 1,
                "signal_ladder": "candidate",
                "status": "pending",
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


def render_weekly_synthesis(root: Path, week: str, intelligence_records: list[dict[str, Any]]) -> None:
    weekly_dir = root / "journal" / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    if not (weekly_dir / "README.md").exists():
        write_text(
            weekly_dir / "README.md",
            "# Weekly Synthesis\n\nStore one markdown file per ISO week named `YYYY-Www.md`.\n",
        )

    candidate_records: list[dict[str, Any]] = []
    for record in intelligence_records:
        candidate_records.extend(record.get("candidate_records", []))
    aggregated = aggregate_candidate_records(candidate_records)
    sessions_count = len(intelligence_records)
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
    lines.extend(["## Promotion Candidates Ready For Review", ""])
    ready = [entry for entry in aggregated if entry["signal_ladder"] != "candidate"][:10]
    if ready:
        lines.extend(render_queue_section(ready))
    else:
        lines.append("- None yet.")
        lines.append("")
    lines.extend(["## Sessions Included", ""])
    if intelligence_records:
        for record in intelligence_records:
            lines.append(
                f"- `{record['session_id']}` | agent=`{record.get('active_agent')}` | mode=`{record.get('active_mode')}` | confidence=`{record.get('confidence')}`"
            )
    else:
        lines.append("- None.")
    lines.append("")
    write_text(weekly_dir / f"{week}.md", "\n".join(lines))


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

    transcript_text = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
    transcript_entries = extract_transcript_entries(transcript_text)
    context_text = context_path.read_text(encoding="utf-8") if context_path.exists() else ""

    emit_progress("summarizing", "summarizing session")
    data = summarize_session(root, manifest, transcript_text, transcript_entries, context_text)
    candidate_records = build_candidate_records(manifest, data)

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

    aggregated = aggregate_candidate_records(load_candidate_records(root))
    write_review_queues(root, aggregated)
    write_json(root / "journal" / "inbox" / "context-cache.json", build_context_cache(aggregated))
    emit_progress("weekly", "building weekly synthesis")
    render_weekly_synthesis(root, weekly_id(manifest["started_at"]), load_weekly_intelligence(root, weekly_id(manifest["started_at"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
