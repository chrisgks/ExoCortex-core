#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import termios
import tty
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_MARKERS = ("AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "system", "domains", "agents")
CONTEXT_FILES = (
    "README.md",
    "AGENT.md",
    "MEMORY.md",
    "STATE.md",
    "WORKFLOWS.md",
    "SKILLS.md",
    "DECISION RULES.md",
    "PERSONA CALIBRATION.md",
    "HEALTH STATE.md",
    "HEALTH RULES.md",
)
WIKI_CONTEXT_FILES = (
    "wiki/index.md",
    "wiki/00_meta/Scope.md",
    "wiki/00_meta/Operating Contract.md",
)
ROOT_PRELOAD_FILES = (
    "README.md",
    "AGENT.md",
    "STATE.md",
)
SYSTEM_PRELOAD_FILES = (
    "README.md",
    "AGENT.md",
    "STATE.md",
    "DECISION RULES.md",
    "PERSONA CALIBRATION.md",
)
SCOPE_PRELOAD_FILES = (
    "README.md",
    "AGENT.md",
    "STATE.md",
    "WORKFLOWS.md",
    "SKILLS.md",
    "DECISION RULES.md",
)
MAX_PRELOAD_FILE_CHARS = 4000
MAX_PRELOAD_TOTAL_CHARS = 40000
INFO_FLAGS = {
    "--help",
    "--version",
    "-h",
}
CLAUDE_SUBCOMMANDS = {
    "agents",
    "auth",
    "auto-mode",
    "doctor",
    "help",
    "install",
    "mcp",
    "plugin",
    "plugins",
    "setup-token",
    "update",
    "upgrade",
}
GEMINI_SUBCOMMANDS = {
    "mcp",
    "extensions",
}
CODEX_SUBCOMMANDS = {
    "exec",
    "review",
    "login",
    "logout",
    "mcp",
    "mcp-server",
    "app-server",
    "app",
    "completion",
    "sandbox",
    "debug",
    "apply",
    "resume",
    "fork",
    "cloud",
    "features",
    "help",
}
CLAUDE_OPTIONS_WITH_VALUES = {
    "--append-system-prompt",
    "--model",
    "--permission-mode",
    "--output-format",
    "--json-schema",
}
CODEX_OPTIONS_WITH_VALUES = {
    "-C",
    "-m",
    "-p",
    "-s",
    "--cd",
    "--config",
    "--model",
    "--profile",
    "--sandbox",
}
GEMINI_OPTIONS_WITH_VALUES = {
    "-m",
    "-p",
    "-i",
    "--model",
    "--prompt",
    "--prompt-interactive",
    "--output-format",
}
ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
ACTIVITY_LOG_MODES = {"bar", "lines", "off"}
ACTIVITY_LOG_DETAILS = {"lifecycle", "inferred", "verbose", "debug"}
MAX_STATUS_EVENTS = 80
WORKER_PROGRESS_PREFIX = "EXOCORTEX_PROGRESS|"
TERMINAL_NOISE_PATTERNS = (
    re.compile(br"\x1b\[(?:200|201)~?"),
    re.compile(br"\x1b\[[IO]"),
)
PHASE_STATUS_MESSAGES = {
    "exploring": "exploring",
    "editing": "editing",
    "running": "running",
    "testing": "testing",
    "waiting": "waiting",
    "summarizing": "summarizing",
}
@dataclass
class Context:
    root: Path
    cwd: Path
    domain: str | None
    project: str | None
    active_agent: str
    active_mode: str
    level: str
    visible_contexts: list[dict[str, Any]]
    health_snapshot: dict[str, str]
    weighted_context: list[dict[str, Any]]


@dataclass
class PreloadedContextFile:
    path: str
    content: str
    truncated: bool
    source_chars: int
    included_chars: int


@dataclass
class PreloadReport:
    active: bool
    files: list[PreloadedContextFile]
    missing_files: list[str]
    total_chars: int
    hit_total_cap: bool


def stream_supports_live_bar(stream: Any) -> bool:
    term = os.environ.get("TERM", "")
    return bool(getattr(stream, "isatty", lambda: False)()) and term.lower() != "dumb"


def activity_log_mode(stream: Any | None = None) -> str:
    requested = os.environ.get("EXOCORTEX_CLI_LOG", "").strip().lower()
    if requested in ACTIVITY_LOG_MODES:
        return requested
    return "lines"


def activity_log_detail() -> str:
    requested = os.environ.get("EXOCORTEX_CLI_LOG_DETAIL", "").strip().lower()
    if requested in ACTIVITY_LOG_DETAILS:
        return requested
    return "inferred"


def compact_status_message(text: str, max_chars: int = 110) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def route_status_message(tool: str, context: Context) -> str:
    return (
        f"{tool} -> agent={context.active_agent}; "
        f"mode={context.active_mode}; level={context.level}"
    )


def visible_context_summary(context: Context) -> str:
    labels = [entry["label"] for entry in context.visible_contexts]
    return ", ".join(labels) if labels else "none"


def context_surface_names(context: Context) -> list[str]:
    labels = [entry["label"] for entry in context.visible_contexts]
    surfaces: list[str] = []

    def add(surface: str) -> None:
        if surface not in surfaces:
            surfaces.append(surface)

    if context.project:
        add("project")
        add("domain")
    elif context.domain:
        add("domain")
    elif any(label.startswith("agent:") for label in labels):
        add("agent")
    elif any(label == "local" for label in labels):
        add("local")
    else:
        add("root")

    if any(label == "system" for label in labels):
        add("system")
    if any(label == "root" for label in labels):
        add("root")
    if any("wiki/" in file_path or "/wiki/" in file_path for entry in context.visible_contexts for file_path in entry["files"]):
        add("wiki")
    return surfaces


def context_surface_summary(context: Context) -> str:
    return ", ".join(context_surface_names(context))


def startup_status_message(context: Context, preload_report: PreloadReport) -> str:
    return f"{context.active_agent} / {context.active_mode} / {context.level} scope"


def should_show_startup_line(tool: str, reporter: ActivityReporter, *, stdin_is_tty: bool) -> bool:
    return reporter.enabled


def startup_context_count_message(report: PreloadReport) -> str:
    file_count = len(report.files)
    file_label = "file" if file_count == 1 else "files"
    return f"using context from {file_count} {file_label}"


def startup_surface_message(context: Context) -> str:
    surfaces = context_surface_summary(context)
    return f"context areas: {surfaces}" if surfaces else "context areas: none"


def startup_file_scope(context: Context, rel_path: str) -> str:
    if rel_path.startswith("system/"):
        return "system"
    if rel_path.startswith("wiki/"):
        return "wiki"
    if context.domain and context.project:
        project_prefix = f"domains/{context.domain}/projects/{context.project}/"
        project_wiki_prefix = project_prefix + "wiki/"
        if rel_path.startswith(project_wiki_prefix):
            return "project wiki"
        if rel_path.startswith(project_prefix):
            return "project"
    if context.domain:
        domain_prefix = f"domains/{context.domain}/"
        domain_wiki_prefix = domain_prefix + "wiki/"
        if rel_path.startswith(domain_wiki_prefix):
            return "domain wiki"
        if rel_path.startswith(domain_prefix):
            return "domain"
    if rel_path.startswith("agents/"):
        return "agent"
    return "root"


def startup_loaded_file_groups(context: Context, report: PreloadReport) -> list[tuple[str, list[str]]]:
    groups: list[tuple[str, list[str]]] = []
    index_by_scope: dict[str, int] = {}
    for item in report.files:
        scope = startup_file_scope(context, item.path)
        if scope in index_by_scope:
            groups[index_by_scope[scope]][1].append(item.path)
            continue
        index_by_scope[scope] = len(groups)
        groups.append((scope, [item.path]))
    return groups


def startup_context_cap_message(report: PreloadReport) -> str | None:
    if not report.hit_total_cap:
        return None
    return "lower-priority context files were skipped because the startup cap was reached"


def append_status_event(
    manifest: dict[str, Any],
    phase: str,
    kind: str,
    message: str,
) -> bool:
    event = {
        "ts": iso_now(),
        "phase": phase,
        "kind": kind,
        "message": compact_status_message(message, max_chars=180),
    }
    events = manifest.setdefault("status_events", [])
    if events and events[-1]["phase"] == event["phase"] and events[-1]["message"] == event["message"]:
        return False
    events.append(event)
    if len(events) > MAX_STATUS_EVENTS:
        del events[:-MAX_STATUS_EVENTS]
    return True


class ActivityReporter:
    def __init__(self, mode: str, detail: str, stream: Any | None = None) -> None:
        self.mode = mode
        self.detail = detail
        self.stream = stream or sys.stderr
        self.phase = ""
        self.message = ""
        self.visible = False

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def allows_inferred(self) -> bool:
        return self.detail in {"inferred", "verbose", "debug"}

    def shows_verbose_details(self) -> bool:
        return self.detail in {"verbose", "debug"}

    def shows_debug_details(self) -> bool:
        return self.detail == "debug"

    def update(self, phase: str, message: str, *, force: bool = False) -> bool:
        if not self.enabled:
            return False
        message = compact_status_message(message)
        if not force and self.phase == phase and self.message == message:
            return False
        self.phase = phase
        self.message = message
        if self.mode == "lines":
            self._write(f"[exo] {phase}: {message}\n")
            return True
        self._render_bar()
        return True

    def note(self, phase: str, message: str) -> None:
        if self.mode != "lines":
            return
        self._write(f"[exo] {phase}: {compact_status_message(message)}\n")

    def pause(self) -> None:
        if self.mode != "bar" or not self.visible:
            return
        self._write("\r\033[2K")
        self.visible = False

    def resume(self) -> None:
        if self.mode != "bar" or not self.enabled or not self.phase:
            return
        self._render_bar()

    def finish(self, phase: str, message: str) -> None:
        if not self.enabled:
            return
        message = compact_status_message(message)
        if self.mode == "bar":
            self.pause()
            self._write(f"[exo] {phase}: {message}\n")
            self.phase = phase
            self.message = message
            return
        self.update(phase, message, force=True)

    def _render_bar(self) -> None:
        self._write(f"\r\033[2K[exo] {self.phase}: {self.message}")
        self.visible = True

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()


def activity_log_stream(*, stdin_is_tty: bool) -> Any:
    if stdin_is_tty:
        return sys.stdout
    return sys.stderr


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def exocortex_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    missing = [marker for marker in ROOT_MARKERS if not (root / marker).exists()]
    if missing:
        raise RuntimeError(f"Could not verify ExoCortex root at {root}; missing {missing}")
    return root


def detect_domain_project(root: Path, cwd: Path) -> tuple[str | None, str | None]:
    root = root.resolve()
    try:
        rel = cwd.resolve().relative_to(root)
    except ValueError:
        return None, None

    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "domains":
        domain = parts[1]
        project = None
        if len(parts) >= 4 and parts[2] == "projects":
            project = parts[3]
        return domain, project
    return None, None


def detect_agent_context(root: Path, cwd: Path) -> str | None:
    root = root.resolve()
    try:
        rel = cwd.resolve().relative_to(root)
    except ValueError:
        return None

    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "agents":
        return parts[1]
    return None


def is_knowledge_subsystem_path(root: Path, cwd: Path) -> bool:
    root = root.resolve()
    try:
        rel = cwd.resolve().relative_to(root)
    except ValueError:
        return False

    parts = rel.parts
    if not parts:
        return False
    if parts[0] == "raw":
        return True
    return "wiki" in parts


def has_codebase_markers(path: Path) -> bool:
    markers = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile")
    return any((path / marker).exists() for marker in markers)


def has_codebase_markers_between(start: Path, stop: Path) -> bool:
    current = start.resolve()
    stop = stop.resolve()
    while True:
        if has_codebase_markers(current):
            return True
        if current == stop or current.parent == current:
            break
        current = current.parent
    return False


def default_agent(domain: str | None, project: str | None, cwd: Path, root: Path) -> str:
    override = os.environ.get("EXOCORTEX_AGENT")
    if override:
        return override
    agent_context = detect_agent_context(root, cwd)
    if agent_context:
        return agent_context
    if is_knowledge_subsystem_path(root, cwd):
        return "knowledge-steward"
    if cwd.resolve() == root.resolve():
        return "chief-of-staff"
    if domain == "work":
        if project:
            project_dir = root / "domains" / domain / "projects" / project
            if has_codebase_markers_between(cwd, project_dir):
                return "builder"
        return "planning"
    if domain == "life":
        return "life-systems"
    if domain == "learning":
        return "research"
    if domain == "writing":
        return "planning"
    return "chief-of-staff"


def default_mode(agent: str) -> str:
    override = os.environ.get("EXOCORTEX_MODE")
    if override:
        return override
    if agent == "builder":
        return "application"
    if agent == "knowledge-steward":
        return "compression"
    if agent == "chief-of-staff":
        return "conversation"
    if agent == "life-systems":
        return "application"
    return "processing"


def level_name(domain: str | None, project: str | None, cwd: Path, root: Path) -> str:
    if cwd.resolve() == root.resolve():
        return "root"
    if project:
        return "project"
    if domain:
        return "domain"
    return "local"


def agent_visible_labels(agent: str) -> set[str]:
    if agent == "builder":
        return {"project", "domain", "root", "agent", "local"}
    if agent == "research":
        return {"project", "domain", "root", "system", "agent", "local"}
    if agent == "planning":
        return {"project", "domain", "root", "system", "agent", "local"}
    if agent == "chief-of-staff":
        return {"project", "domain", "root", "system", "agent", "local"}
    if agent == "knowledge-steward":
        return {"project", "domain", "root", "system", "agent", "local"}
    if agent == "life-systems":
        return {"project", "domain", "root", "system", "agent", "local"}
    return {"project", "domain", "root", "agent", "local"}


def context_paths(root: Path, cwd: Path, domain: str | None, project: str | None) -> list[tuple[str, Path]]:
    root = root.resolve()
    entries: list[tuple[str, Path]] = [("root", root)]
    seen: set[Path] = {root}

    system_dir = (root / "system").resolve()
    if system_dir.exists():
        entries.append(("system", system_dir))
        seen.add(system_dir)

    try:
        rel = cwd.resolve().relative_to(root)
    except ValueError:
        return entries

    current = root.resolve()
    for part in rel.parts:
        current = current / part
        if current in seen:
            continue
        seen.add(current)
        path = Path(current)
        label = "local"
        rel_path = path.relative_to(root)
        if len(rel_path.parts) == 2 and rel_path.parts[0] == "agents":
            label = f"agent:{rel_path.parts[1]}"
        elif domain and path.resolve() == (root / "domains" / domain).resolve():
            label = f"domain:{domain}"
        elif project and path.resolve() == (root / "domains" / domain / "projects" / project).resolve():
            label = f"project:{project}"
        entries.append((label, path))

    return entries


def collect_context(root: Path, cwd: Path, agent: str, mode: str) -> Context:
    root = root.resolve()
    cwd = cwd.resolve()
    domain, project = detect_domain_project(root, cwd)
    visible: list[dict[str, Any]] = []
    for label, path in context_paths(root, cwd, domain, project):
        visible_label = label.split(":")[0]
        if visible_label not in agent_visible_labels(agent):
            continue
        present = []
        for name in CONTEXT_FILES:
            file_path = path / name
            if file_path.exists():
                present.append(str(file_path.relative_to(root)))
        for name in WIKI_CONTEXT_FILES:
            file_path = path / name
            if file_path.exists():
                present.append(str(file_path.relative_to(root)))
        if present:
            visible.append({"label": label, "path": "." if path.resolve() == root.resolve() else str(path.relative_to(root)), "files": present})

    if agent == "builder":
        # Keep builder narrow by stripping root personal memory files from direct context listing.
        for entry in visible:
            if entry["label"] == "root":
                entry["files"] = [
                    item
                    for item in entry["files"]
                    if Path(item).name not in {"MEMORY.md", "SKILLS.md"}
                ]

    return Context(
        root=root,
        cwd=cwd,
        domain=domain,
        project=project,
        active_agent=agent,
        active_mode=mode,
        level=level_name(domain, project, cwd, root),
        visible_contexts=visible,
        health_snapshot=read_health_snapshot(root, agent),
        weighted_context=read_weighted_context(root, agent, domain, project),
    )


def parse_markdown_kv(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        data[key.strip()] = value.strip()
    return data


def read_health_snapshot(root: Path, agent: str) -> dict[str, str]:
    path = root / "system" / "HEALTH STATE.md"
    data = parse_markdown_kv(path)
    if not data:
        return {}
    if agent == "builder":
        keys = (
            "last_updated",
            "energy_now",
            "stress_load_now",
            "cognitive_readiness_now",
            "carryover_fatigue",
            "confidence",
            "adaptation_confidence",
            "response_pacing",
            "question_load",
            "scope_bias",
        )
    elif agent == "chief-of-staff":
        keys = (
            "last_updated",
            "sources",
            "sleep_status",
            "recovery",
            "energy_now",
            "stress_load_now",
            "cognitive_readiness_now",
            "emotional_state_now",
            "exercise_status",
            "carryover_fatigue",
            "carryover_stress",
            "sleep_trend",
            "recovery_trend",
            "load_trend",
            "recent_window_days",
            "confidence",
            "adaptation_confidence",
            "response_pacing",
            "question_load",
            "scope_bias",
            "tone",
            "should_ask_checkin",
        )
    else:
        keys = (
            "last_updated",
            "sleep_status",
            "recovery",
            "energy_now",
            "stress_load_now",
            "cognitive_readiness_now",
            "carryover_fatigue",
            "carryover_stress",
            "sleep_trend",
            "recovery_trend",
            "load_trend",
            "confidence",
            "adaptation_confidence",
            "response_pacing",
            "question_load",
            "scope_bias",
            "should_ask_checkin",
        )
    return {key: data[key] for key in keys if key in data}


def preload_scope_entries(context: Context) -> list[dict[str, Any]]:
    entries = [
        entry
        for entry in context.visible_contexts
        if entry["label"] not in {"root", "system"}
    ]
    return sorted(
        entries,
        key=lambda entry: len(Path(entry["path"]).parts),
        reverse=True,
    )


def join_relative(base: str, name: str) -> str:
    if base in {"", "."}:
        return name
    return str(Path(base) / name)


def should_preload_wiki_contract(context: Context) -> bool:
    return is_knowledge_subsystem_path(context.root, context.cwd)


def authoritative_preload_candidates(context: Context) -> list[str]:
    rel_paths: list[str] = []
    seen: set[str] = set()

    def add(rel_path: str) -> None:
        normalized = str(Path(rel_path))
        if normalized in seen:
            return
        seen.add(normalized)
        rel_paths.append(normalized)

    active_entries = preload_scope_entries(context)
    for entry in active_entries:
        for name in SCOPE_PRELOAD_FILES:
            add(join_relative(entry["path"], name))

    for entry in active_entries:
        wiki_candidates = [join_relative(entry["path"], name) for name in WIKI_CONTEXT_FILES]
        if any((context.root / candidate).exists() for candidate in wiki_candidates):
            for candidate in wiki_candidates:
                add(candidate)
            break

    if should_preload_wiki_contract(context):
        add(str(Path("wiki") / "00_meta" / "Operating Contract.md"))

    for name in SYSTEM_PRELOAD_FILES:
        add(str(Path("system") / name))

    for name in ROOT_PRELOAD_FILES:
        add(name)
    for name in WIKI_CONTEXT_FILES:
        if name == "wiki/00_meta/Operating Contract.md" and not should_preload_wiki_contract(context):
            continue
        add(name)

    return rel_paths


def load_authoritative_preload(
    context: Context,
    per_file_chars: int = MAX_PRELOAD_FILE_CHARS,
    total_chars: int = MAX_PRELOAD_TOTAL_CHARS,
) -> PreloadReport:
    candidates = authoritative_preload_candidates(context)
    files: list[PreloadedContextFile] = []
    missing_files: list[str] = []
    remaining = total_chars
    hit_total_cap = False

    for index, rel_path in enumerate(candidates):
        path = context.root / rel_path
        if not path.exists():
            missing_files.append(rel_path)
            continue
        if remaining <= 0:
            hit_total_cap = True
            break

        text = path.read_text(encoding="utf-8")
        cap = min(per_file_chars, remaining)
        included = text[:cap]
        truncated = len(text) > cap
        files.append(
            PreloadedContextFile(
                path=rel_path,
                content=included,
                truncated=truncated,
                source_chars=len(text),
                included_chars=len(included),
            )
        )
        remaining -= len(included)
        if remaining <= 0 and index < len(candidates) - 1:
            hit_total_cap = True

    return PreloadReport(
        active=bool(files),
        files=files,
        missing_files=missing_files,
        total_chars=sum(item.included_chars for item in files),
        hit_total_cap=hit_total_cap,
    )


def compact_line(text: str, max_chars: int = 180) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def render_authoritative_preload(report: PreloadReport) -> list[str]:
    lines = ["- Startup context manifest:"]
    if not report.files:
        lines.append("  - file: none")
        return lines

    lines.append(
        "  - The wrapper resolved these authoritative files for this run. Treat them as the active context surface."
    )
    lines.append("  - Read only the smallest relevant subset when needed; do not front-load repository reads.")
    if report.hit_total_cap:
        lines.append("  - file: lower-priority files were omitted because the manifest hit the cap")
    for item in report.files:
        lines.append(f"  - file: {item.path}")
    return lines


def format_visible_contexts(visible_contexts: list[dict[str, Any]]) -> list[str]:
    lines = ["- Context files available by scope:"]
    for entry in visible_contexts:
        files = ", ".join(entry["files"])
        lines.append(f"  - {entry['label']}: {files}")
    return lines


def is_low_signal_weighted_context(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return True
    if re.match(r"^-{1,2}\s*[a-z0-9]", normalized.lower()):
        return True
    if re.match(r"^[a-z0-9-]+\s{2,}[A-Z]", text):
        return True
    if re.match(r"^[a-z0-9-]+\s+[A-Z].*\(", normalized):
        return True
    return False


def render_weighted_context(items: list[dict[str, Any]], limit: int = 4) -> list[str]:
    rendered = ["- Reusable context:"]
    kept = 0
    for item in items:
        raw_text = item["text"]
        text = compact_line(raw_text)
        if is_low_signal_weighted_context(raw_text):
            continue
        rendered.append(f"  - reuse: {item['candidate_type']} -> {text}")
        kept += 1
        if kept >= limit:
            break
    if kept == 0:
        return []
    return rendered


def health_checkin_guidance(context: Context) -> str | None:
    health_snapshot = context.health_snapshot
    if not health_snapshot:
        return None
    if context.active_mode in {"application", "compression"}:
        return None
    if context.active_agent in {"builder", "knowledge-steward"}:
        return None

    confidence = health_snapshot.get("confidence", "").lower()
    should_ask = health_snapshot.get("should_ask_checkin", "").lower()
    if should_ask not in {"yes", "true"}:
        return None

    unknown_count = sum(1 for value in health_snapshot.values() if value.lower() == "unknown")
    if confidence not in {"low", "unknown", ""} and unknown_count < 3:
        return None

    if context.active_agent == "chief-of-staff" and context.active_mode == "conversation":
        question = "Should I keep this tight and concrete, or make space to think out loud?"
    elif context.active_agent == "life-systems":
        question = "Should I optimize for low-friction progress or for a fuller rethink?"
    else:
        question = "Do you want a narrow next step, or a broader pass first?"

    return (
        "Adapt silently by default. Ask at most one brief operational question only if it would materially "
        f"improve pacing, scope, or tone. Suggested question: {question}"
    )


def render_health_summary(context: Context) -> list[str]:
    health_snapshot = context.health_snapshot
    if not health_snapshot:
        return []

    primary_fields = (
        ("energy_now", "energy"),
        ("stress_load_now", "stress"),
        ("cognitive_readiness_now", "readiness"),
        ("confidence", "confidence"),
    )
    adaptation_fields = (
        ("response_pacing", "pacing"),
        ("question_load", "questions"),
        ("scope_bias", "scope"),
        ("tone", "tone"),
    )

    lines = ["- Health summary:"]
    primary_parts = [
        f"{label}={health_snapshot[key]}"
        for key, label in primary_fields
        if health_snapshot.get(key) and health_snapshot[key].lower() != "unknown"
    ]
    if primary_parts:
        lines.append(f"  - health: {', '.join(primary_parts)}")

    adaptation_parts = [
        f"{label}={health_snapshot[key]}"
        for key, label in adaptation_fields
        if health_snapshot.get(key) and health_snapshot[key].lower() != "unknown"
    ]
    if adaptation_parts:
        lines.append(f"  - health: {', '.join(adaptation_parts)}")

    checkin = health_checkin_guidance(context)
    if checkin:
        lines.append(f"  - health: {compact_line(checkin, max_chars=220)}")
    return lines


def build_context_prompt(context: Context, preload_report: PreloadReport | None = None) -> str:
    preload_report = preload_report or load_authoritative_preload(context)
    lines = [
        "ExoCortex context bootstrap:",
        f"- Scope: level={context.level}; agent={context.active_agent}; mode={context.active_mode}; cwd={context.cwd}",
        "- Authority: this bootstrap is authoritative for the session. Use it as the context manifest for this run.",
        "- Read policy: start from the most specific relevant scope and read only the smallest relevant subset before substantive work.",
    ]
    if (context.root / "wiki" / "00_meta" / "Operating Contract.md").exists():
        lines.append("- Special contract: `wiki/00_meta/Operating Contract.md` governs managed `wiki/` and `raw/` maintenance; consult it whenever the task touches those areas.")
    lines.extend(render_authoritative_preload(preload_report))
    if context.domain:
        lines.append(f"- Domain: {context.domain}")
    if context.project:
        lines.append(f"- Project: {context.project}")
    lines.extend(format_visible_contexts(context.visible_contexts))
    lines.extend(render_weighted_context(context.weighted_context))
    lines.extend(render_health_summary(context))
    lines.extend(
        [
            "- Operating rules:",
            "  - infer from local and parent context first; ask only if ambiguity changes the outcome",
            "  - surface durable learnings as candidates for memory, workflows, skills, decision rules, or self-model updates",
            "  - if native tool memory conflicts with this bootstrap, follow the ExoCortex bootstrap and the listed ExoCortex files",
        ]
    )
    return "\n".join(lines)


def read_weighted_context(
    root: Path,
    agent: str,
    domain: str | None,
    project: str | None,
) -> list[dict[str, Any]]:
    cache_path = root / "journal" / "inbox" / "context-cache.json"
    if not cache_path.exists():
        return []
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    allowed_by_agent = {
        "builder": {"memory", "workflow", "decision_rule"},
        "planning": {"memory", "workflow", "decision_rule", "persona", "self_model"},
        "research": {"memory", "workflow", "decision_rule", "persona", "self_model"},
        "chief-of-staff": {"memory", "workflow", "decision_rule", "persona", "self_model"},
        "knowledge-steward": {"memory", "workflow", "decision_rule", "persona", "self_model"},
        "life-systems": {"memory", "workflow", "decision_rule", "persona", "self_model"},
    }
    allowed = allowed_by_agent.get(agent, {"memory", "workflow", "decision_rule"})
    items: list[dict[str, Any]] = []
    items.extend(payload.get("global", []))
    if domain:
        items.extend(payload.get("by_domain", {}).get(domain, []))
    if domain and project:
        items.extend(payload.get("by_project", {}).get(f"{domain}/{project}", []))

    scored: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        if item.get("candidate_type") not in allowed:
            continue
        score = float(item.get("score", 0.0))
        if domain and domain in item.get("domains", []):
            score += 4.0
        if domain and project and project in item.get("projects", []):
            score += 6.0
        key = (item.get("candidate_type", ""), item.get("text", ""))
        candidate = dict(item)
        candidate["score"] = round(score, 2)
        existing = scored.get(key)
        if existing is None or candidate["score"] > existing["score"]:
            scored[key] = candidate

    per_type_limits = {"memory": 2, "workflow": 2, "decision_rule": 2, "persona": 2, "self_model": 2}
    selected: list[dict[str, Any]] = []
    for candidate_type, limit in per_type_limits.items():
        matches = [item for item in scored.values() if item["candidate_type"] == candidate_type]
        matches.sort(key=lambda item: item["score"], reverse=True)
        selected.extend(matches[:limit])
    selected.sort(key=lambda item: item["score"], reverse=True)
    return selected[:8]


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def normalize_terminal_line(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    cleaned = cleaned.lstrip("•").strip()
    cleaned = re.sub(r"^[\u2514\u2500\s]+", "", cleaned)
    return cleaned


class StreamLineBuffer:
    def __init__(self, apply_user_backspaces: bool = False) -> None:
        self.apply_user_backspaces = apply_user_backspaces
        self.pending = ""

    def feed(self, text: str) -> list[str]:
        normalized = strip_ansi(text).replace("\r\n", "\n")
        emitted: list[str] = []
        for char in normalized:
            if self.apply_user_backspaces and char in {"\x08", "\x7f"}:
                self.pending = self.pending[:-1]
                continue
            if char == "\r":
                self.pending = ""
                continue
            if char == "\n":
                line = self.pending.rstrip()
                if line:
                    emitted.append(line)
                self.pending = ""
                continue
            self.pending += char
        return emitted

    def flush(self) -> list[str]:
        line = self.pending.rstrip()
        self.pending = ""
        return [line] if line else []


class TranscriptLogger:
    def __init__(self, handle: Any) -> None:
        self.handle = handle
        self.buffers = {
            "user": StreamLineBuffer(apply_user_backspaces=True),
            "tool": StreamLineBuffer(),
        }

    def write(self, role: str, data: bytes) -> list[str]:
        text = data.decode("utf-8", errors="replace")
        lines = self.buffers[role].feed(text)
        for line in lines:
            self.handle.write(f"[{role}] {line}\n")
        self.handle.flush()
        return lines

    def finalize(self) -> None:
        for role, buffer in self.buffers.items():
            for line in buffer.flush():
                self.handle.write(f"[{role}] {line}\n")
        self.handle.flush()


class TerminalNoiseFilter:
    def __init__(self) -> None:
        self.pending = b""

    def feed(self, data: bytes) -> bytes:
        combined = self.pending + data
        self.pending = b""
        for pattern in TERMINAL_NOISE_PATTERNS:
            combined = pattern.sub(b"", combined)

        last_escape = combined.rfind(b"\x1b")
        if last_escape != -1 and len(combined) - last_escape <= 8:
            self.pending = combined[last_escape:]
            combined = combined[:last_escape]
        return combined


def filter_terminal_input(filter_: TerminalNoiseFilter, data: bytes) -> tuple[bytes, bool]:
    if not data:
        return b"", True
    return filter_.feed(data), False


def classify_activity_line(tool: str, line: str) -> tuple[str, str] | None:
    normalized = normalize_terminal_line(line)
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered.startswith("[exo]") or lowered.startswith(WORKER_PROGRESS_PREFIX.lower()):
        return None

    if "run /review" in lowered or lowered.startswith("review ") or "/review" in lowered:
        return ("reviewing", normalized)
    if normalized.startswith("Explored") or normalized.startswith("Read ") or normalized.startswith("Search ") or normalized.startswith("List "):
        return ("exploring", normalized)
    if normalized.startswith("Edited") or normalized.startswith("Updated ") or normalized.startswith("Implemented") or normalized.startswith("Patched"):
        return ("editing", normalized)
    if normalized.startswith("Waited for background terminal") or normalized.startswith("Waiting for background terminal"):
        return ("waiting", normalized)
    if normalized.startswith("Ran "):
        command = normalized[4:].strip()
        test_markers = ("test", "build", "pytest", "unittest", "vite", "npm run", "cargo", "go test")
        phase = "testing" if any(marker in command.lower() for marker in test_markers) else "running"
        return (phase, command or normalized)
    if normalized.startswith("Building ") or "test suite" in lowered:
        return ("testing", normalized)
    if normalized.startswith("Summary") or normalized.startswith("Summar") or "weekly synthesis" in lowered:
        return ("summarizing", normalized)

    if tool != "codex":
        generic_markers = (
            ("read ", "exploring"),
            ("edited ", "editing"),
            ("updated ", "editing"),
            ("ran ", "running"),
        )
        for marker, phase in generic_markers:
            if lowered.startswith(marker):
                return (phase, normalized)
    return None


def parse_worker_progress(line: str) -> tuple[str, str] | None:
    if not line.startswith(WORKER_PROGRESS_PREFIX):
        return None
    _, _, payload = line.partition(WORKER_PROGRESS_PREFIX)
    phase, _, message = payload.partition("|")
    phase = phase.strip()
    message = message.strip()
    if not phase or not message:
        return None
    return (phase, message)


def report_status(
    reporter: ActivityReporter,
    manifest: dict[str, Any],
    phase: str,
    kind: str,
    message: str,
    *,
    force: bool = False,
) -> None:
    changed = append_status_event(manifest, phase, kind, message)
    if changed or force:
        reporter.update(phase, message, force=force)


def report_inferred_activity(
    tool: str,
    reporter: ActivityReporter,
    manifest: dict[str, Any],
    phase: str,
    message: str,
) -> None:
    if not reporter.allows_inferred():
        return
    if tool == "codex" and reporter.detail == "inferred":
        return
    if reporter.detail == "inferred":
        if reporter.phase == phase:
            return
        message = PHASE_STATUS_MESSAGES.get(phase, phase)
    report_status(reporter, manifest, phase, "inferred", message)


def run_interactive_session(
    argv: list[str],
    cwd: Path,
    transcript: Any,
    tool: str,
    reporter: ActivityReporter,
    manifest: dict[str, Any],
) -> int:
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    logger = TranscriptLogger(transcript)
    input_filter = TerminalNoiseFilter()
    output_filter = TerminalNoiseFilter()
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stdin_open = True
    old_tty = None

    if os.isatty(stdin_fd):
        old_tty = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)

    try:
        append_status_event(manifest, "active", "lifecycle", f"{tool} session active")
        while True:
            read_fds = [master_fd]
            if stdin_open:
                read_fds.append(stdin_fd)

            ready, _, _ = select.select(read_fds, [], [], 0.05)

            if master_fd in ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if data:
                    data = output_filter.feed(data)
                if data:
                    reporter.pause()
                    os.write(stdout_fd, data)
                    lines = logger.write("tool", data)
                    for line in lines:
                        inferred = classify_activity_line(tool, line)
                        if not inferred:
                            continue
                        phase, message = inferred
                        report_inferred_activity(tool, reporter, manifest, phase, message)

            if stdin_open and stdin_fd in ready:
                try:
                    data = os.read(stdin_fd, 4096)
                except OSError:
                    data = b""
                data, stdin_closed = filter_terminal_input(input_filter, data)
                if data:
                    reporter.pause()
                    os.write(master_fd, data)
                    logger.write("user", data)
                if stdin_closed:
                    stdin_open = False

            if proc.poll() is not None:
                drained = False
                while not drained:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        data = b""
                    if not data:
                        drained = True
                        continue
                    data = output_filter.feed(data)
                    if not data:
                        continue
                    reporter.pause()
                    os.write(stdout_fd, data)
                    lines = logger.write("tool", data)
                    for line in lines:
                        inferred = classify_activity_line(tool, line)
                        if not inferred:
                            continue
                        phase, message = inferred
                        report_inferred_activity(tool, reporter, manifest, phase, message)
                break
    finally:
        if old_tty is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty)
        logger.finalize()
        reporter.pause()
        os.close(master_fd)

    return proc.wait()


def run_session_worker(
    root: Path,
    worker: Path,
    manifest_path: Path,
    reporter: ActivityReporter,
    manifest: dict[str, Any],
) -> int:
    env = dict(os.environ)
    env["EXOCORTEX_PROGRESS"] = "1"
    proc = subprocess.Popen(
        [sys.executable, str(worker), str(manifest_path)],
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    with proc.stdout:
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            progress = parse_worker_progress(line)
            if progress:
                phase, message = progress
                report_status(reporter, manifest, phase, "worker", message)
                continue
            if reporter.shows_debug_details() and line.strip():
                reporter.note("postprocess", line.strip())
    return proc.wait()


def find_real_binary(tool: str) -> str:
    env_name = f"EXOCORTEX_REAL_{tool.upper()}"
    if env_name in os.environ:
        return os.environ[env_name]

    wrapper_dir = Path(__file__).resolve().parent / "bin"
    path_items = [
        item
        for item in os.environ.get("PATH", "").split(os.pathsep)
        if item and Path(item).resolve() != wrapper_dir.resolve()
    ]
    real = shutil.which(tool, path=os.pathsep.join(path_items))
    if not real:
        raise RuntimeError(
            f"Could not locate underlying binary for {tool}. Set {env_name} to the real path."
        )
    return real


def claude_needs_prompt_injection(args: list[str]) -> bool:
    if has_cli_option(args, INFO_FLAGS):
        return False
    positional = first_positional_arg(args, CLAUDE_OPTIONS_WITH_VALUES)
    if positional is None:
        return True
    return positional not in CLAUDE_SUBCOMMANDS


def codex_needs_prompt_injection(args: list[str]) -> bool:
    if has_cli_option(args, INFO_FLAGS):
        return False
    positional = first_positional_arg(args, CODEX_OPTIONS_WITH_VALUES)
    if positional is None:
        return True
    return positional not in CODEX_SUBCOMMANDS


def first_positional_arg(args: list[str], options_with_values: set[str]) -> str | None:
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            break
        if arg in options_with_values:
            skip_next = True
            continue
        if arg.startswith("--") and "=" in arg:
            continue
        if arg.startswith("-"):
            continue
        return arg
    return None


def first_positional_index(args: list[str], options_with_values: set[str]) -> int | None:
    skip_next = False
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            break
        if arg in options_with_values:
            skip_next = True
            continue
        if arg.startswith("--") and "=" in arg:
            continue
        if arg.startswith("-"):
            continue
        return index
    return None


def has_cli_option(args: list[str], option_names: set[str]) -> bool:
    for arg in args:
        if arg in option_names:
            return True
        if arg.startswith("--") and "=" in arg:
            option_name = arg.split("=", 1)[0]
            if option_name in option_names:
                return True
    return False


def gemini_needs_prompt_injection(args: list[str]) -> bool:
    if has_cli_option(args, INFO_FLAGS):
        return False
    positional = first_positional_arg(args, GEMINI_OPTIONS_WITH_VALUES)
    if positional is None:
        return True
    return positional not in GEMINI_SUBCOMMANDS


def combine_prompts(exocortex_prompt: str, user_prompt: str) -> str:
    user_prompt = user_prompt.strip()
    if not user_prompt:
        return exocortex_prompt
    return (
        f"{exocortex_prompt}\n\n"
        "User request:\n"
        f"{user_prompt}"
    )


def codex_developer_instruction_args(prompt: str) -> list[str]:
    return ["-c", f"developer_instructions={json.dumps(prompt)}"]


def build_codex_developer_instructions(context_path: str) -> str:
    return "\n".join(
        [
            "ExoCortex bootstrap is authoritative for this session.",
            f"Read `{context_path}` first.",
            "Treat the listed files as a context manifest, not a mandate to open everything up front.",
            "Read only the smallest relevant subset when needed.",
        ]
    )


def replace_first_positional_arg(args: list[str], options_with_values: set[str], new_value: str) -> list[str]:
    updated = list(args)
    index = first_positional_index(updated, options_with_values)
    if index is None:
        return updated
    updated[index] = new_value
    return updated


def replace_option_value(args: list[str], option_names: set[str], transform: Any) -> list[str]:
    updated: list[str] = []
    skip_next = False
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg in option_names and index + 1 < len(args):
            updated.extend([arg, transform(args[index + 1])])
            skip_next = True
            continue
        if any(arg.startswith(f"{name}=") for name in option_names if name.startswith("--")):
            option_name, _, value = arg.partition("=")
            updated.append(f"{option_name}={transform(value)}")
            continue
        updated.append(arg)
    return updated


def inject_args(
    tool: str,
    args: list[str],
    prompt: str,
    *,
    stdin_is_tty: bool = True,
    context_path: str | None = None,
) -> list[str]:
    if tool == "claude" and claude_needs_prompt_injection(args):
        return ["--append-system-prompt", prompt, *args]
    if tool == "codex" and codex_needs_prompt_injection(args):
        codex_prompt = prompt
        if context_path:
            codex_prompt = build_codex_developer_instructions(context_path)
        return [*codex_developer_instruction_args(codex_prompt), *args]
    if tool == "gemini" and gemini_needs_prompt_injection(args):
        if has_cli_option(args, {"-i", "--prompt-interactive"}):
            return replace_option_value(args, {"-i", "--prompt-interactive"}, lambda value: combine_prompts(prompt, value))
        if has_cli_option(args, {"-p", "--prompt"}):
            return replace_option_value(args, {"-p", "--prompt"}, lambda value: combine_prompts(prompt, value))
        has_positional = first_positional_arg(args, GEMINI_OPTIONS_WITH_VALUES) is not None
        if has_positional:
            user_prompt = first_positional_arg(args, GEMINI_OPTIONS_WITH_VALUES) or ""
            return replace_first_positional_arg(
                args,
                GEMINI_OPTIONS_WITH_VALUES,
                combine_prompts(prompt, user_prompt),
            )
        if not has_positional and stdin_is_tty:
            return ["--prompt-interactive", prompt, *args]
    return args


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: exocortex_wrapper.py <tool> [args...]", file=sys.stderr)
        return 2

    tool = sys.argv[1]
    passthrough_args = sys.argv[2:]

    root = exocortex_root()
    cwd = Path.cwd()
    agent = default_agent(*detect_domain_project(root, cwd), cwd, root)
    mode = default_mode(agent)
    context = collect_context(root, cwd, agent, mode)
    preload_report = load_authoritative_preload(context)
    prompt = build_context_prompt(context, preload_report)
    stdin_is_tty = os.isatty(sys.stdin.fileno())
    log_stream = activity_log_stream(stdin_is_tty=stdin_is_tty)
    log_mode = activity_log_mode(log_stream)
    log_detail = activity_log_detail()
    reporter = ActivityReporter(log_mode, log_detail, log_stream)
    startup_message = startup_status_message(context, preload_report)

    started_at = iso_now()
    date_str = started_at[:10]
    session_id = str(uuid.uuid4())
    session_dir = root / "journal" / "sessions" / date_str
    session_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = session_dir / f"{session_id}.transcript.md"
    manifest_path = session_dir / f"{session_id}.json"
    context_path = session_dir / f"{session_id}.context.md"
    summary_path = session_dir / f"{session_id}.summary.md"
    candidates_path = session_dir / f"{session_id}.candidates.md"

    context_path.write_text(prompt + "\n", encoding="utf-8")

    manifest: dict[str, Any] = {
        "session_id": session_id,
        "tool": tool,
        "argv": passthrough_args,
        "cwd": str(cwd),
        "root": str(root),
        "started_at": started_at,
        "ended_at": None,
        "exit_code": None,
        "active_agent": agent,
        "active_mode": mode,
        "domain": context.domain,
        "project": context.project,
        "level": context.level,
        "health_snapshot": context.health_snapshot,
        "transcript_path": str(transcript_path.relative_to(root)),
        "context_path": str(context_path.relative_to(root)),
        "summary_path": str(summary_path.relative_to(root)),
        "candidates_path": str(candidates_path.relative_to(root)),
        "summary_status": "pending",
        "activity_log_mode": log_mode,
        "activity_detail": log_detail,
        "status_events": [],
    }
    append_status_event(manifest, "boot", "lifecycle", f"resolved {tool} in {context.level} scope")
    append_status_event(manifest, "route", "context", route_status_message(tool, context))
    append_status_event(manifest, "capture", "lifecycle", f"prepared session {session_id[:8]}")
    append_status_event(manifest, "startup", "context", startup_message)
    if should_show_startup_line(tool, reporter, stdin_is_tty=stdin_is_tty):
        if reporter.mode == "bar":
            reporter.finish("startup", startup_message)
        else:
            reporter.update("startup", startup_message, force=True)
    if reporter.mode == "lines" and preload_report.files:
        reporter.note("context", startup_surface_message(context))
        reporter.note("context", startup_context_count_message(preload_report))
        for scope, paths in startup_loaded_file_groups(context, preload_report):
            reporter.note("scope", scope)
            for path in paths:
                reporter.note("loaded", path)
        cap_message = startup_context_cap_message(preload_report)
        if cap_message:
            reporter.note("context", cap_message)
    if reporter.shows_verbose_details():
        reporter.note("route", route_status_message(tool, context))
        reporter.note("context", f"visible={visible_context_summary(context)}")
    if reporter.shows_debug_details():
        reporter.note("capture", f"session={session_id[:8]}; cwd={cwd}; context_path={context_path}")
    write_json(manifest_path, manifest)

    real_binary = find_real_binary(tool)
    exec_args = inject_args(
        tool,
        passthrough_args,
        prompt,
        stdin_is_tty=stdin_is_tty,
        context_path=str(context_path),
    )
    argv = [real_binary, *exec_args]
    append_status_event(manifest, "launch", "lifecycle", f"starting {tool}")
    if reporter.shows_debug_details():
        reporter.note("launch", f"starting {tool}")

    transcript = transcript_path.open("w", encoding="utf-8")
    transcript.write("# Session Transcript\n\n")
    transcript.write(f"- session_id: `{session_id}`\n")
    transcript.write(f"- tool: `{tool}`\n")
    transcript.write(f"- active_agent: `{agent}`\n")
    transcript.write(f"- active_mode: `{mode}`\n")
    transcript.write(f"- cwd: `{cwd}`\n")
    transcript.write(f"- started_at: `{started_at}`\n\n")
    transcript.write("## Stream\n\n")
    transcript.flush()

    old_cwd = os.getcwd()
    exit_code = 0
    try:
        os.chdir(cwd)
        exit_code = run_interactive_session(argv, cwd, transcript, tool, reporter, manifest)
    finally:
        os.chdir(old_cwd)
        transcript.close()

    ended_at = iso_now()
    manifest["ended_at"] = ended_at
    manifest["exit_code"] = exit_code
    manifest["summary_status"] = "processing"
    report_status(reporter, manifest, "postprocess", "lifecycle", "processing session artifacts")
    write_json(manifest_path, manifest)

    worker = root / "tools" / "workers" / "process_session.py"
    result_code = run_session_worker(root, worker, manifest_path, reporter, manifest)
    manifest["summary_status"] = "complete" if result_code == 0 else "failed"
    if result_code == 0:
        reporter.finish("done", "session artifacts complete")
        append_status_event(manifest, "done", "lifecycle", "session artifacts complete")
    else:
        reporter.finish("failed", "post-processing failed")
        append_status_event(manifest, "failed", "lifecycle", "post-processing failed")
    write_json(manifest_path, manifest)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
