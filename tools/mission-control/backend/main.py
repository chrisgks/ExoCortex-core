from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import re
import subprocess
from pathlib import Path
import frontmatter
from typing import List, Optional, Dict
from pydantic import BaseModel
import datetime
import sys

# Ensure project root is in path for internal tools imports
EXOCORTEX_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.append(str(EXOCORTEX_ROOT))

from tools.workers import intent_review, process_session as worker
from tools.wrappers import exocortex_wrapper as wrapper

app = FastAPI(title="ExoCortex Mission Control API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

EXOCORTEX_ROOT = Path(__file__).parent.parent.parent.parent
QUEUE_TYPE_MAP = {
    "memory": "memory",
    "workflows": "workflow",
    "rules": "decision_rule",
    "skills": "skill",
    "intents": "intent",
    "self-model": "self_model",
    "persona": "persona",
    "questions": "question",
}
TARGET_MAP = {
    "memory": "MEMORY.md",
    "workflow": "WORKFLOWS.md",
    "decision_rule": "DECISION RULES.md",
    "skill": "SKILLS.md",
    "self_model": "system/SELF MODEL.md",
    "persona": "system/PERSONA CALIBRATION.md",
    "question": "system/QUESTIONING.md",
}
KNOWN_MODES = (
    "ingestion",
    "conversation",
    "processing",
    "compression",
    "application",
    "synthesis",
)
LOCAL_POLICY_FILES = {
    "README.md",
    "STATE.md",
    "WORKFLOWS.md",
    "MEMORY.md",
    "DECISION RULES.md",
    "SKILLS.md",
}

class ProjectState(BaseModel):
    path: str
    name: str
    current_focus: Optional[str] = None
    active_agent: Optional[str] = None
    status: str = "idle"
    friction_score: float = 0.0

class Thought(BaseModel):
    content: str

class Candidate(BaseModel):
    id: str
    type: str
    content: str
    evidence_count: int = 1
    source_file: str
    block_start_line: Optional[int] = None
    suggested_destination: Optional[str] = None
    review_recommendation: Optional[str] = None
    intent_stage: Optional[str] = None
    queue_section: Optional[str] = None

class HealthUpdate(BaseModel):
    energy_now: str
    cognitive_readiness_now: str
    stress_load_now: str

class AgentExecution(BaseModel):
    agent: str
    mode: str
    context: str
    prompt: str


def normalize_queue_type(name: str) -> str:
    return QUEUE_TYPE_MAP.get(name, name.rstrip("s"))


def default_title(path: Path) -> str:
    return path.stem.replace("_", " ")


def append_bullet_once(path: Path, content: str) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = f"# {default_title(path)}\n\n"
    bullet = f"- {content}"
    if bullet in text:
        return
    if not text.endswith("\n"):
        text += "\n"
    if not text.rstrip():
        text = f"# {default_title(path)}\n\n"
    text = text.rstrip() + "\n\n" + bullet + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def resolve_context_path(root: Path, requested: str | None) -> tuple[Path, str]:
    relative = requested or "."
    candidate = Path(relative)
    if candidate.is_absolute():
        raise HTTPException(status_code=400, detail="Action-space path must be relative to the ExoCortex root.")
    resolved = (root / candidate).resolve()
    try:
        rel = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Action-space path must remain inside the ExoCortex root.") from exc
    if resolved.is_file():
        resolved = resolved.parent
        rel = resolved.relative_to(root.resolve())
    if not resolved.exists() or not resolved.is_dir():
        raise HTTPException(status_code=404, detail=f"Context path '{relative}' was not found.")
    return resolved, "." if str(rel) == "." else str(rel)


def context_display_label(entry: dict) -> str:
    label = entry["label"]
    if ":" in label:
        prefix, value = label.split(":", 1)
        return f"{prefix} / {value}"
    return label


def list_available_agents(root: Path) -> list[str]:
    agents_dir = root / "agents"
    if not agents_dir.exists():
        return []
    return sorted(item.name for item in agents_dir.iterdir() if item.is_dir())


def extract_referenced_skills(root: Path, visible_contexts: list[dict]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(r"`(skills/[^`]+)`")
    for entry in visible_contexts:
        for file_name in entry.get("files", []):
            if Path(file_name).name != "SKILLS.md":
                continue
            path = root / file_name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for ref in pattern.findall(text):
                normalized = ref.rstrip("/")
                if normalized in seen:
                    continue
                seen.add(normalized)
                refs.append(
                    {
                        "path": normalized,
                        "label": Path(normalized).name or normalized,
                        "source": file_name,
                    }
                )
    return refs


def current_context_entry(context: wrapper.Context, relative_path: str) -> dict:
    for entry in context.visible_contexts:
        if entry["path"] == relative_path:
            return entry
    return {"label": "current", "path": relative_path, "files": []}


def build_action_space(root: Path, requested_path: str | None = None, agent: str | None = None, mode: str | None = None) -> dict:
    cwd, relative_path = resolve_context_path(root, requested_path)
    domain, project = wrapper.detect_domain_project(root, cwd)
    active_agent = agent or wrapper.default_agent(domain, project, cwd, root)
    active_mode = mode or wrapper.default_mode(active_agent)
    context = wrapper.collect_context(root, cwd, active_agent, active_mode)
    current_entry = current_context_entry(context, relative_path)
    relevant_skills = extract_referenced_skills(root, context.visible_contexts)
    alternate_agents = [name for name in list_available_agents(root) if name != active_agent][:6]
    alternate_modes = [name for name in KNOWN_MODES if name != active_mode]
    local_targets = [
        file_name
        for file_name in current_entry.get("files", [])
        if Path(file_name).name in LOCAL_POLICY_FILES
    ][:6]
    journal_targets = ["journal/sessions/", "journal/inbox/review-queue.md"]
    if (root / "journal" / "inbox" / "pending-intents.md").exists():
        journal_targets.append("journal/inbox/pending-intents.md")

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_nodes: set[str] = set()

    def add_node(node_id: str, **payload: object) -> None:
        if node_id in seen_nodes:
            return
        seen_nodes.add(node_id)
        node = {"id": node_id}
        node.update(payload)
        nodes.append(node)

    def add_edge(source: str, target: str, kind: str, reason: str) -> None:
        edges.append({"from": source, "to": target, "kind": kind, "reason": reason})

    center_context_id = f"context:{relative_path}"
    add_node(
        center_context_id,
        kind="context",
        column="contexts",
        label=context_display_label(current_entry),
        subtitle=relative_path,
        active=True,
        recenter_path=relative_path,
    )
    for entry in context.visible_contexts:
        node_id = f"context:{entry['path']}"
        add_node(
            node_id,
            kind="context",
            column="contexts",
            label=context_display_label(entry),
            subtitle=entry["path"],
            active=entry["path"] == relative_path,
            recenter_path=entry["path"],
        )

    agent_id = f"policy:agent:{active_agent}"
    mode_id = f"policy:mode:{active_mode}"
    visibility_id = "policy:visibility"
    add_node(agent_id, kind="policy", column="policies", label=f"Agent / {active_agent}", subtitle="active routing role")
    add_node(mode_id, kind="policy", column="policies", label=f"Mode / {active_mode}", subtitle="active operating mode")
    add_node(
        visibility_id,
        kind="policy",
        column="policies",
        label=f"{len(context.visible_contexts)} visible contexts",
        subtitle="local + ancestor surfaces",
    )
    add_edge(center_context_id, agent_id, "active", "Resolved from folder location and local defaults.")
    add_edge(center_context_id, mode_id, "active", "Default mode for the active agent.")
    add_edge(center_context_id, visibility_id, "available", "Visible contexts collected by the wrapper.")

    if context.health_snapshot:
        health_id = "policy:health"
        add_node(
            health_id,
            kind="policy",
            column="policies",
            label="Health overlay",
            subtitle=f"{len(context.health_snapshot)} active fields",
        )
        add_edge(center_context_id, health_id, "available", "Health overlay is available for this context.")
    else:
        health_id = None

    if context.weighted_context:
        weighted_id = "policy:weighted"
        add_node(
            weighted_id,
            kind="policy",
            column="policies",
            label=f"{len(context.weighted_context)} reusable signals",
            subtitle="weighted prior context",
        )
        add_edge(center_context_id, weighted_id, "available", "Weighted reusable context was loaded for this session.")
    else:
        weighted_id = None

    if relevant_skills:
        skills_id = "policy:skills"
        add_node(
            skills_id,
            kind="policy",
            column="policies",
            label=f"{len(relevant_skills)} skill references",
            subtitle="visible from SKILLS.md",
        )
        add_edge(center_context_id, skills_id, "available", "Referenced by visible SKILLS.md files.")
    else:
        skills_id = None

    action_specs = [
        ("action:act_local", "Act locally", "Work in the current folder using the active context."),
        ("action:move_context", "Move context", "Recenter on a visible parent, child, or sibling context."),
        ("action:switch_agent", "Switch agent", "Change the role while keeping the same filesystem entrypoint."),
        ("action:switch_mode", "Switch mode", "Change the cognitive operation without changing topic."),
        ("action:invoke_skill", "Invoke skill", "Use a referenced shipped skill."),
        ("action:ask_user", "Ask user", "Request the missing constraint when ambiguity changes the outcome."),
        ("action:write_state", "Write durable state", "Update local or system markdown contracts."),
        ("action:write_journal", "Write journal", "Record or review session artifacts."),
        ("action:promote_signal", "Promote signal", "Review inferred signal before durable promotion."),
    ]
    for node_id, label, subtitle in action_specs:
        add_node(node_id, kind="action", column="actions", label=label, subtitle=subtitle)
        add_edge(agent_id, node_id, "available", "The active agent can choose this class of move.")
        add_edge(mode_id, node_id, "available", "Mode influences how this move is carried out.")
    add_edge(visibility_id, "action:move_context", "available", "Other visible contexts are available from the wrapper.")
    if health_id:
        add_edge(health_id, "action:ask_user", "requires_user", "Low-confidence health context may justify a check-in.")
    if weighted_id:
        add_edge(weighted_id, "action:act_local", "available", "Weighted context can guide local action.")
    if skills_id:
        add_edge(skills_id, "action:invoke_skill", "available", "Referenced skills can be invoked here.")

    current_target_id = f"target:current:{relative_path}"
    add_node(
        current_target_id,
        kind="target",
        column="targets",
        label="Current folder",
        subtitle=relative_path,
        group="context",
        recenter_path=relative_path,
    )
    add_edge("action:act_local", current_target_id, "available", "Local action can proceed in the current folder.")

    for entry in context.visible_contexts:
        if entry["path"] == relative_path:
            continue
        node_id = f"target:context:{entry['path']}"
        add_node(
            node_id,
            kind="target",
            column="targets",
            label=context_display_label(entry),
            subtitle=entry["path"],
            group="context",
            recenter_path=entry["path"],
        )
        add_edge("action:move_context", node_id, "available", "The wrapper already exposes this context surface.")

    for name in alternate_agents:
        node_id = f"target:agent:{name}"
        add_node(node_id, kind="target", column="targets", label=f"Agent / {name}", subtitle="alternate static role", group="agent")
        add_edge("action:switch_agent", node_id, "available", "Static agent roles are available under agents/.")

    for name in alternate_modes:
        node_id = f"target:mode:{name}"
        add_node(node_id, kind="target", column="targets", label=f"Mode / {name}", subtitle="alternate operating mode", group="mode")
        add_edge("action:switch_mode", node_id, "available", "Modes are global runtime options.")

    for skill in relevant_skills[:6]:
        node_id = f"target:skill:{skill['path']}"
        add_node(
            node_id,
            kind="target",
            column="targets",
            label=f"Skill / {skill['label']}",
            subtitle=skill["path"],
            group="skill",
        )
        add_edge("action:invoke_skill", node_id, "available", f"Referenced from {skill['source']}.")

    user_id = "target:user"
    add_node(user_id, kind="target", column="targets", label="User clarification", subtitle="requires direct confirmation", group="user")
    add_edge("action:ask_user", user_id, "requires_user", "The user resolves ambiguity that local policy cannot.")

    for file_name in local_targets:
        node_id = f"target:file:{file_name}"
        add_node(
            node_id,
            kind="target",
            column="targets",
            label=Path(file_name).name,
            subtitle=file_name,
            group="file",
        )
        add_edge("action:write_state", node_id, "available", "This local durable file is present in the current context.")

    if not local_targets:
        state_target_id = "target:file:STATE.md"
        add_node(state_target_id, kind="target", column="targets", label="STATE.md", subtitle="current context state surface", group="file")
        add_edge("action:write_state", state_target_id, "available", "Most serious contexts should expose a STATE.md surface.")

    for path_name in journal_targets:
        node_id = f"target:journal:{path_name}"
        add_node(node_id, kind="target", column="targets", label=Path(path_name.rstrip('/')).name or path_name, subtitle=path_name, group="journal")
        add_edge("action:write_journal", node_id, "available", "Journal artifacts are globally available.")

    promote_targets = []
    if (root / "journal" / "inbox" / "pending-intents.md").exists():
        promote_targets.append("journal/inbox/pending-intents.md")
    if (root / "system" / "OPEN LOOPS.md").exists():
        promote_targets.append("system/OPEN LOOPS.md")
    if (root / "system" / "PRIORITIES.md").exists():
        promote_targets.append("system/PRIORITIES.md")
    for path_name in promote_targets[:3]:
        node_id = f"target:promotion:{path_name}"
        add_node(node_id, kind="target", column="targets", label=Path(path_name).name, subtitle=path_name, group="promotion")
        add_edge("action:promote_signal", node_id, "requires_user", "Promotion should follow the review loop, not silent automation.")

    return {
        "center": {
            "path": relative_path,
            "level": context.level,
            "agent": active_agent,
            "mode": active_mode,
            "domain": context.domain,
            "project": context.project,
        },
        "nodes": nodes,
        "edges": edges,
    }


def parse_queue_candidates(source_file: str, content: str) -> list[Candidate]:
    current_type = normalize_queue_type(source_file.replace("pending-", "").replace(".md", ""))
    candidates: list[Candidate] = []
    lines = content.splitlines()
    current_section: Optional[str] = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            current_section = line.removeprefix("## ").strip()
            i += 1
            continue
        if not line.startswith("### "):
            i += 1
            continue

        content_text = line.removeprefix("### ").strip()
        block_start = i
        i += 1
        metadata: dict[str, str] = {}
        while i < len(lines) and not lines[i].startswith("### ") and not lines[i].startswith("## "):
            stripped = lines[i].strip()
            if stripped.startswith("- ") and ": " in stripped:
                key, value = stripped[2:].split(": ", 1)
                metadata[key.strip()] = value.strip().strip("`")
            i += 1

        if content_text.lower() == "none queued.":
            continue
        if current_type == "intent" and current_section and current_section.lower().startswith("review rules"):
            continue
        candidates.append(
            Candidate(
                id=f"{source_file}:{block_start + 1}",
                type=current_type,
                content=content_text,
                evidence_count=int(metadata.get("evidence_count", "1") or "1"),
                source_file=source_file,
                block_start_line=block_start + 1,
                suggested_destination=metadata.get("suggested_destination"),
                review_recommendation=metadata.get("review_recommendation"),
                intent_stage=metadata.get("intent_stage"),
                queue_section=current_section,
            )
        )
    return candidates


def remove_candidate_block(path: Path, candidate: Candidate) -> None:
    if not path.exists() or candidate.block_start_line is None:
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    start = candidate.block_start_line - 1
    if start < 0 or start >= len(lines):
        return
    if lines[start].strip() != f"### {candidate.content}":
        return
    end = start + 1
    while end < len(lines) and not lines[end].startswith("### ") and not lines[end].startswith("## "):
        end += 1
    new_lines = lines[:start] + lines[end:]
    while new_lines and new_lines[-1] == "":
        new_lines.pop()
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def refresh_review_queues() -> None:
    aggregated = worker.aggregate_candidate_records(worker.load_candidate_records(EXOCORTEX_ROOT))
    worker.write_review_queues(EXOCORTEX_ROOT, aggregated)


def promote_intent_candidate(candidate: Candidate) -> str:
    records = intent_review.load_reviewable_records(EXOCORTEX_ROOT)
    record = intent_review.choose_record(records, candidate.content)
    if candidate.intent_stage == "confirmed_open_loop" or candidate.review_recommendation == "promote_priority":
        promotion_text = intent_review.default_promotion_text(record.get("promotion_text") or record["text"])
        intent_review.append_priority(EXOCORTEX_ROOT, record, promotion_text)
        intent_review.record_review_decision(
            EXOCORTEX_ROOT,
            record,
            stage="priority",
            promoted_to="system/PRIORITIES.md",
            promotion_text=promotion_text,
        )
        refresh_review_queues()
        return "system/PRIORITIES.md"

    promotion_text = intent_review.default_promotion_text(record["text"])
    intent_review.append_open_loop(EXOCORTEX_ROOT, record, promotion_text)
    intent_review.record_review_decision(
        EXOCORTEX_ROOT,
        record,
        stage="confirmed_open_loop",
        promoted_to="system/OPEN LOOPS.md",
        promotion_text=promotion_text,
    )
    refresh_review_queues()
    return "system/OPEN LOOPS.md"


def promote_generic_candidate(candidate: Candidate) -> str:
    target_rel = candidate.suggested_destination or TARGET_MAP.get(candidate.type, "MEMORY.md")
    target_path = EXOCORTEX_ROOT / target_rel
    append_bullet_once(target_path, candidate.content)
    source_path = EXOCORTEX_ROOT / "journal" / "inbox" / candidate.source_file
    remove_candidate_block(source_path, candidate)
    return str(target_rel)

@app.post("/api/offload")
async def offload_thought(thought: Thought):
    open_loops_path = EXOCORTEX_ROOT / "system" / "OPEN LOOPS.md"
    if not open_loops_path.exists():
        open_loops_path.write_text("# Open Loops\n\n")
    
    with open(open_loops_path, "a") as f:
        f.write(f"- {thought.content} (recorded: {datetime.datetime.now().isoformat()})\n")
    return {"status": "success"}

@app.post("/api/health/update")
async def update_health(update: HealthUpdate):
    health_path = EXOCORTEX_ROOT / "system" / "HEALTH STATE.md"
    content = health_path.read_text()
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        if "energy_now:" in line: new_lines.append(f"- energy_now: {update.energy_now}")
        elif "cognitive_readiness_now:" in line: new_lines.append(f"- cognitive_readiness_now: {update.cognitive_readiness_now}")
        elif "stress_load_now:" in line: new_lines.append(f"- stress_load_now: {update.stress_load_now}")
        else: new_lines.append(line)
    health_path.write_text("\n".join(new_lines))
    return {"status": "success"}

@app.get("/api/triage")
async def get_triage():
    inbox_dir = EXOCORTEX_ROOT / "journal" / "inbox"
    candidates = []
    if not inbox_dir.exists(): return {"candidates": []}
    file = inbox_dir / "pending-intents.md"
    if file.exists():
        candidates.extend(parse_queue_candidates(file.name, file.read_text(encoding="utf-8")))
    return {"candidates": candidates}

@app.post("/api/triage/promote")
async def promote_candidate(candidate: Candidate):
    if candidate.type != "intent":
        raise HTTPException(status_code=400, detail="Mission Control triage currently supports intent review only.")
    target_rel = promote_intent_candidate(candidate)
    return {"status": "promoted", "target": str(target_rel)}


@app.post("/api/triage/reject")
async def reject_candidate(candidate: Candidate):
    if candidate.type != "intent":
        raise HTTPException(status_code=400, detail="Mission Control triage currently supports intent review only.")
    records = intent_review.load_reviewable_records(EXOCORTEX_ROOT)
    record = intent_review.choose_record(records, candidate.content)
    intent_review.record_review_decision(
        EXOCORTEX_ROOT,
        record,
        stage="rejected",
        promotion_text=record["text"],
    )
    refresh_review_queues()
    return {"status": "rejected"}

@app.get("/api/radar")
async def get_radar():
    """
    Scans for projects and calculates friction based on session density vs output.
    """
    active_projects = []
    domains_dir = EXOCORTEX_ROOT / "domains"
    summaries_dir = EXOCORTEX_ROOT / "journal" / "summarised"

    if not domains_dir.exists(): return {"projects": []}

    # 1. Map session density per folder
    friction_map = {}
    if summaries_dir.exists():
        for file in summaries_dir.glob("*.md"):
            content = file.read_text()
            # Extract CWD from session headers
            matches = re.findall(r"Session in `.*?/(domains/.*?)`", content)
            for match in matches:
                # Basic scoring: start with 0.1 per session
                # In real use, we'd subtract for completed tasks
                friction_map[match] = friction_map.get(match, 0) + 0.2
                if "None extracted" in content: # Heuristic for low output
                    friction_map[match] += 0.3

    for root, dirs, files in os.walk(domains_dir):
        if "STATE.md" in files:
            state_path = Path(root) / "STATE.md"
            rel_path = state_path.relative_to(EXOCORTEX_ROOT)
            proj_rel = str(rel_path.parent)

            post = frontmatter.load(state_path)
            focus = post.get("current_focus")
            if not focus:
                for line in post.content.split("\n"):
                    if line.strip().startswith("- "):
                        focus = line.strip("- ").strip()
                        break

            # Normalize friction score
            raw_friction = friction_map.get(proj_rel, 0.0)
            friction = min(raw_friction, 1.0)

            active_projects.append(ProjectState(
                path=proj_rel,
                name=rel_path.parent.name,
                current_focus=focus,
                status="active" if focus else "idle",
                friction_score=friction
            ))
    return {"projects": active_projects}

@app.get("/api/action-space")
async def get_action_space(path: str = ".", agent: Optional[str] = None, mode: Optional[str] = None):
    return build_action_space(EXOCORTEX_ROOT, requested_path=path, agent=agent, mode=mode)

@app.get("/api/telemetry")
async def get_telemetry():
    summaries_dir = EXOCORTEX_ROOT / "journal" / "summarised"
    if not summaries_dir.exists(): return {"modes": {}, "agents": {}}
    mode_counts = {}
    agent_counts = {}
    
    # Robust parsing of summaries
    hourly_output = {} # hour -> total decisions
    for file in sorted(summaries_dir.glob("*.md"), reverse=True)[:14]:
        content = file.read_text()
        # Find session start times and decisions
        blocks = content.split("## ")
        for block in blocks[1:]:
            lines = block.split("\n")
            header = lines[0]
            # Extract hour from ## 2026-04-12 01:54:55
            time_match = re.search(r"(\d{2}):\d{2}:\d{2}", header)
            if time_match:
                hour = int(time_match.group(1))
                decisions = len(re.findall(r"- ", block.split("### Decisions")[1].split("###")[0])) if "### Decisions" in block else 0
                hourly_output[hour] = hourly_output.get(hour, 0) + decisions

            sessions = re.findall(r"using agent `.*?` in mode `(.*?)`", block)
            for mode in sessions:
                mode_counts[mode] = mode_counts.get(mode, 0) + 1

    # Identify peak hour
    peak_hour = max(hourly_output, key=hourly_output.get) if hourly_output else 0

    return {
        "modes": mode_counts,
        "agents": agent_counts,
        "total_sessions": sum(mode_counts.values()),
        "peak_hour": f"{peak_hour:02}:00",
        "hourly_distribution": hourly_output
    }
@app.post("/api/execute")
async def execute_agent(req: AgentExecution):
    """
    Executes an agent wrapper and returns the output.
    """
    wrapper_path = EXOCORTEX_ROOT / "tools" / "wrappers" / "bin" / "gemini"
    if not wrapper_path.exists():
        raise HTTPException(status_code=500, detail="Gemini wrapper not found")
    
    try:
        # Run the wrapper in a specific directory
        # Using -p/--prompt for non-interactive execution
        result = subprocess.run(
            [str(wrapper_path), "-p", req.prompt, "--agent", req.agent, "--mode", req.mode],
            cwd=EXOCORTEX_ROOT / req.context,
            capture_output=True,
            text=True,
            timeout=60
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
    except Exception as e:
        return {"error": str(e)}

class CalendarEvent(BaseModel):
    name: str
    schedule: str
    purpose: str
    command: str

class OpenLoop(BaseModel):
    id: int
    content: str

@app.get("/api/project-cockpit")
async def get_project_cockpit(project_path: str):
    """
    Fetches the full 'contract' for a specific project.
    """
    base_path = EXOCORTEX_ROOT / project_path
    if not base_path.exists():
        raise HTTPException(status_code=404, detail="Project not found")
        
    def get_file_lines(filename: str):
        path = base_path / filename
        if path.exists():
            content = path.read_text(encoding="utf-8")
            # Extract bullet points
            return [l.strip() for l in content.split("\n") if l.strip().startswith("- ")]
        return []

    return {
        "path": project_path,
        "state": get_file_lines("STATE.md"),
        "memory": get_file_lines("MEMORY.md"),
        "rules": get_file_lines("DECISION RULES.md")
    }

@app.get("/api/isomorph")
async def get_isomorphs():
    """
    Finds structural similarities using the Gemini model for semantic understanding.
    """
    memory_files = list(EXOCORTEX_ROOT.rglob("MEMORY.md"))
    if len(memory_files) < 2:
        return {"isomorphs": []}

    # 1. Collect memory content
    domain_memories = {}
    for file in memory_files:
        domain = file.parent.parent.name if "projects" in str(file) else file.parent.name
        content = file.read_text(encoding="utf-8")
        if len(content.strip()) > 50:
            domain_memories[domain] = content[:1000] # Limit context

    # 2. Use Gemini to find semantic bridges (Simplified for the mockup logic)
    # In a real impl, we'd loop and compare pairs with wrapper.run_harness()
    # For now, we perform a keyword-to-concept expansion logic
    isomorphs = []
    domains = list(domain_memories.keys())
    
    # Pre-defined high-level cognitive patterns
    patterns = {
        "bottlenecks": ["dependency", "blocking", "throttle", "constraint"],
        "decoupling": ["isolated", "separation", "interface", "modular"],
        "entropy": ["drift", "mess", "organization", "maintenance"],
        "persistence": ["durable", "memory", "storage", "recall"]
    }
    
    for i in range(len(domains)):
        for j in range(i + 1, len(domains)):
            d1, d2 = domains[i], domains[j]
            shared = []
            for concept, keywords in patterns.items():
                if any(k in domain_memories[d1].lower() for k in keywords) and \
                   any(k in domain_memories[d2].lower() for k in keywords):
                    shared.append(concept)
            
            if shared:
                isomorphs.append({
                    "domains": [d1, d2],
                    "shared_concepts": shared,
                    "resonance": len(shared) / 4.0,
                    "explanation": f"Structural resonance detected in {shared[0]}. Both domains are managing identical systemic constraints."
                })
                
    return {"isomorphs": isomorphs}

@app.get("/api/graph")
async def get_graph():
    """
    Scans the repository for markdown files and builds a link graph.
    """
    nodes = []
    edges = []
    
    # Scan system and domains for .md files
    search_dirs = [EXOCORTEX_ROOT / "system", EXOCORTEX_ROOT / "domains", EXOCORTEX_ROOT / "agents"]
    
    file_map = {}
    
    for s_dir in search_dirs:
        if not s_dir.exists(): continue
        for file in s_dir.rglob("*.md"):
            rel_path = file.relative_to(EXOCORTEX_ROOT)
            node_id = str(rel_path)
            nodes.append({
                "id": node_id,
                "name": file.name,
                "group": str(rel_path.parent).split("/")[0]
            })
            file_map[file.name.replace(".md", "")] = node_id

    # Secondary pass for edges (wikilinks like [[File]])
    for node in nodes:
        path = EXOCORTEX_ROOT / node["id"]
        content = path.read_text()
        links = re.findall(r"\[\[(.*?)\]\]", content)
        for link in links:
            # Simple link matching
            link_clean = link.split("|")[0]
            if link_clean in file_map:
                edges.append({"source": node["id"], "target": file_map[link_clean]})
                
    return {"nodes": nodes, "links": edges}

@app.get("/api/loops")
async def get_loops():
    """
    Fetches raw thoughts from system/OPEN LOOPS.md for triage.
    """
    path = EXOCORTEX_ROOT / "system" / "OPEN LOOPS.md"
    if not path.exists(): return {"loops": []}
    
    content = path.read_text()
    loops = []
    for i, line in enumerate(content.split("\n")):
        if line.strip().startswith("- "):
            loops.append({"id": i, "content": line.strip("- ").strip()})
    return {"loops": loops}

@app.post("/api/loops/route")
async def route_loop(loop: OpenLoop, target_project: str):
    """
    Moves a thought from global open loops to a specific project's inbox.
    """
    # 1. Remove from global
    path = EXOCORTEX_ROOT / "system" / "OPEN LOOPS.md"
    lines = path.read_text().split("\n")
    new_lines = [l for l in lines if loop.content not in l]
    path.write_text("\n".join(new_lines))
    
    # 2. Append to project STATE.md or INBOX.md (using STATE.md for now)
    proj_path = EXOCORTEX_ROOT / target_project / "STATE.md"
    if proj_path.exists():
        with open(proj_path, "a") as f:
            f.write(f"\n- [ ] {loop.content} (routed from Mission Control)\n")
            
    return {"status": "routed"}

@app.post("/api/activate")
async def activate_momentum(project_path: str):
    """
    The 'Momentum Engine' - Opens the relevant project file in Obsidian.
    Uses the 'open' command on macOS to trigger the default markdown handler.
    """
    target = EXOCORTEX_ROOT / project_path / "STATE.md"
    if target.exists():
        # This will open the file in your default editor (likely Obsidian)
        subprocess.run(["open", str(target)])
        return {"status": "activated", "file": str(target)}
    return {"status": "error", "message": "File not found"}

class MarkdownEdit(BaseModel):
    file_path: str
    original_line: str
    new_line: Optional[str] = None # If None, delete the line
    action: str # "edit" or "delete"

@app.post("/api/edit-contract")
async def edit_markdown_contract(edit: MarkdownEdit):
    """
    Surgically edits or deletes a specific line in a markdown file.
    Used for the 'Correction-During-Review' feature.
    """
    target_path = EXOCORTEX_ROOT / edit.file_path
    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Read, modify, and write
    content = target_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    
    new_lines = []
    edit_made = False
    
    for line in lines:
        # Exact match logic (could be improved with line indexing)
        if line.strip() == edit.original_line.strip() and not edit_made:
            if edit.action == "edit" and edit.new_line:
                new_lines.append(edit.new_line)
                edit_made = True
            elif edit.action == "delete":
                edit_made = True
                continue # Skip appending to delete
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
            
    if not edit_made:
        return {"status": "error", "message": "Original line not found"}
        
    target_path.write_text("\n".join(new_lines), encoding="utf-8")
    return {"status": "success", "action": edit.action}

@app.get("/api/calendar")
async def get_calendar():
    """
    Parses system/CRONJOBS.md for scheduled tasks.
    """
    cron_path = EXOCORTEX_ROOT / "system" / "CRONJOBS.md"
    events = []
    
    if not cron_path.exists():
        return {"events": []}

    content = cron_path.read_text()
    # Basic parsing for mockup - look for ### headers and subsequent fields
    sections = content.split("### ")
    for section in sections[1:]: # Skip the intro
        lines = section.split("\n")
        name = lines[0].strip()
        schedule = ""
        purpose = ""
        command = ""
        
        for line in lines:
            if "- schedule: `" in line:
                schedule = line.split("`")[1]
            elif "- purpose: " in line:
                purpose = line.split("- purpose: ")[1]
            elif "- command: `" in line:
                command = line.split("`")[1]
                
        events.append(CalendarEvent(
            name=name,
            schedule=schedule,
            purpose=purpose,
            command=command
        ))
                
    return {"events": events}

@app.get("/api/journal")
async def get_journal():
    summaries_dir = EXOCORTEX_ROOT / "journal" / "summarised"
    timeline = []
    if not summaries_dir.exists(): return {"events": []}
    for file in sorted(summaries_dir.glob("*.md"), reverse=True)[:5]:
        content = file.read_text()
        date_str = file.name.replace(".md", "")
        current_event = None
        for line in content.split("\n"):
            if line.startswith("## "):
                if current_event: timeline.append(current_event)
                parts = line.strip("# ").split(" ")
                current_event = {
                    "date": date_str,
                    "time": parts[1] if len(parts) > 1 else "",
                    "tool": parts[2] if len(parts) > 2 else "system",
                    "agent": parts[4] if len(parts) > 4 else "unknown",
                    "summary": ""
                }
            elif line.startswith("### Summary") and current_event: continue
            elif current_event and not line.startswith("#"):
                current_event["summary"] += line + " "
        if current_event: timeline.append(current_event)
    return {"events": timeline}

@app.get("/api/agents")
async def get_agents():
    agents_dir = EXOCORTEX_ROOT / "agents"
    agents_list = []
    if not agents_dir.exists(): return {"agents": []}
    for d in agents_dir.iterdir():
        if d.is_dir():
            agent_md = d / "AGENT.md"
            description = "Static Role"
            if agent_md.exists():
                lines = agent_md.read_text().split("\n")
                for line in lines:
                    if line and not line.startswith("#"):
                        description = line[:100] + "..."
                        break
            agents_list.append({"name": d.name, "description": description, "status": "ready"})
    return {"agents": agents_list}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
