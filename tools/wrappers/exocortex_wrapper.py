#!/usr/bin/env python3

from __future__ import annotations

import fcntl
import json
import os
import platform
import pty
import queue
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import tty
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workers import usage as usage_worker


ROOT_MARKERS = ("AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "system", "domains", "agents")
CONTEXT_FILES = (
    "README.md",
    "AGENT.md",
    "MEMORY.md",
    "STATE.md",
    "WORKFLOWS.md",
    "SKILLS.md",
    "DECISION RULES.md",
    "INTELLIGENCE LOOP.md",
    "INTENDED BEHAVIORS.md",
    "SELF MODEL.md",
    "PERSONA CALIBRATION.md",
    "HEALTH STATE.md",
    "HEALTH RULES.md",
)
WIKI_CONTEXT_FILES = (
    "wiki/index.md",
    "wiki/00_meta/Scope.md",
    "wiki/00_meta/Operating Contract.md",
)
SURFACE_NOW_FILE = "journal/inbox/surface-now.md"
BRIEF_FILE = "journal/inbox/brief.md"
ROOT_PRELOAD_FILES = (
    "README.md",
    "AGENT.md",
    "STATE.md",
    "wiki-map.md",
)
SYSTEM_PRELOAD_FILES = (
    "README.md",
    "AGENT.md",
    "STATE.md",
    "DECISION RULES.md",
    "INTELLIGENCE LOOP.md",
    "INTENDED BEHAVIORS.md",
    "USAGE RATES.json",
    "SELF MODEL.md",
    "PERSONA CALIBRATION.md",
)
SCOPE_PRELOAD_FILES = (
    "README.md",
    "AGENT.md",
    "STATE.md",
    "MEMORY.md",
    "SKILLS.md",
    "DECISION RULES.md",
)
MAX_PRELOAD_FILE_CHARS = 4000
MAX_PRELOAD_TOTAL_CHARS = 40000
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
CODEX_SESSION_MATCH_SLOP_SECONDS = 10
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


def _get_window_size() -> tuple[int, int]:
    try:
        size = shutil.get_terminal_size(fallback=(24, 80))
        return size.lines, size.columns
    except Exception:
        return 24, 80


def _set_pty_window_size(fd: int, rows: int, cols: int) -> None:
    try:
        tiocswinsz = 0x80087467 if platform.system() == "Darwin" else 0x5414
        fcntl.ioctl(fd, tiocswinsz, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass


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
        # Serializes stderr writes and state mutation so the main relay thread
        # (which calls pause()) cannot interleave with the AsyncIOPipeline
        # background thread (which may call update()).
        self._lock = threading.RLock()

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
        with self._lock:
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
        with self._lock:
            self._write(f"[exo] {phase}: {compact_status_message(message)}\n")

    def pause(self) -> None:
        if self.mode != "bar":
            return
        with self._lock:
            if not self.visible:
                return
            self._write("\r\033[2K")
            self.visible = False

    def resume(self) -> None:
        if self.mode != "bar" or not self.enabled:
            return
        with self._lock:
            if not self.phase:
                return
            self._render_bar()

    def finish(self, phase: str, message: str) -> None:
        if not self.enabled:
            return
        message = compact_status_message(message)
        with self._lock:
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
    markers = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile", "requirements.txt")
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


_AGENT_ROLE_RE = re.compile(r"Default agent[^:]*:\s*`([\w-]+)`")


def read_agent_role_from_agent_md(cwd: Path, root: Path) -> str | None:
    """Walk from cwd up to (but not including) root, return the first role
    declared as 'Default agent ...: `role`' in an AGENT.md file."""
    current = cwd.resolve()
    root = root.resolve()
    while current != root and current.parent != current:
        agent_md = current / "AGENT.md"
        if agent_md.is_file():
            try:
                text = agent_md.read_text(encoding="utf-8", errors="ignore")
                m = _AGENT_ROLE_RE.search(text)
                if m:
                    return m.group(1)
            except OSError:
                pass
        current = current.parent
    return None


def infer_agent_from_request(request_text: str) -> str | None:
    lowered = request_text.lower()
    if not lowered.strip():
        return None
    if re.search(r"\b(review|audit|critique|risk|regression|pre-release|quality)\b", lowered):
        return "reviewer"
    if re.search(r"\b(research|look up|source|evidence|compare|latest|literature|paper|cite)\b", lowered):
        return "research"
    if re.search(r"\b(ingest|wiki|raw source|source note|promote memory|knowledge steward|maintain wiki)\b", lowered):
        return "knowledge-steward"
    if re.search(r"\b(plan|roadmap|sequence|break down|prioriti[sz]e|scope|strategy)\b", lowered):
        return "planning"
    if re.search(r"\b(implement|fix|debug|test|refactor|build|ship|patch|code)\b", lowered):
        return "builder"
    return None


def user_request_from_args(tool: str, args: list[str]) -> str:
    parts: list[str] = []
    options_with_values = {
        "claude": CLAUDE_OPTIONS_WITH_VALUES,
        "codex": CODEX_OPTIONS_WITH_VALUES,
        "gemini": GEMINI_OPTIONS_WITH_VALUES,
    }.get(tool, set())
    prompt_value_options = {
        "claude": set(),
        "codex": set(),
        "gemini": {"-p", "--prompt", "--prompt-interactive"},
    }.get(tool, set())
    skip_next = False
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg in options_with_values:
            if index + 1 < len(args):
                if arg in prompt_value_options:
                    parts.append(args[index + 1])
                skip_next = True
            continue
        if arg.startswith("-"):
            continue
        if arg in CLAUDE_SUBCOMMANDS or arg in CODEX_SUBCOMMANDS or arg in GEMINI_SUBCOMMANDS:
            continue
        parts.append(arg)
    return " ".join(parts).strip()


def default_agent(
    domain: str | None,
    project: str | None,
    cwd: Path,
    root: Path,
    request_text: str | None = None,
) -> str:
    override = os.environ.get("EXOCORTEX_AGENT")
    if override:
        return override
    agent_context = detect_agent_context(root, cwd)
    if agent_context:
        return agent_context
    declared = read_agent_role_from_agent_md(cwd, root)
    if declared:
        return declared
    if is_knowledge_subsystem_path(root, cwd):
        return "knowledge-steward"
    inferred = infer_agent_from_request(request_text or "")
    if inferred:
        return inferred
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


def _has_nonempty_content(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def should_preload_surface_now(context: Context) -> bool:
    return _has_nonempty_content(context.root / SURFACE_NOW_FILE)


def should_preload_brief(context: Context) -> bool:
    return _has_nonempty_content(context.root / BRIEF_FILE)


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

    # The Brief is the single read surface. It incorporates
    # the surface-now items, so when it exists we preload it and drop surface-now
    # to keep the manifest DRY. Surface-now stays the fallback until a Brief is built.
    if should_preload_brief(context):
        add(BRIEF_FILE)
    elif should_preload_surface_now(context):
        add(SURFACE_NOW_FILE)

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
    lazy = lazy_bootstrap_enabled()

    for index, rel_path in enumerate(candidates):
        path = context.root / rel_path
        if not path.exists():
            missing_files.append(rel_path)
            continue
        if remaining <= 0:
            hit_total_cap = True
            break

        if lazy:
            try:
                source_chars = path.stat().st_size
            except OSError:
                missing_files.append(rel_path)
                continue
            cap = min(per_file_chars, remaining)
            included_chars = min(cap, source_chars)
            files.append(
                PreloadedContextFile(
                    path=rel_path,
                    content="",
                    truncated=source_chars > cap,
                    source_chars=source_chars,
                    included_chars=included_chars,
                )
            )
            remaining -= included_chars
        else:
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
        "- Attribution tagging (DO THIS inline, every turn it applies): when a NAMED ExoCortex input — a specific memory, decision rule, brief item, persona note, or self-model entry — materially changes what you say or decide, mark it at that exact point as `[exo: applied <name> — <short why>]`. Worked example: \"I'll keep this terse [exo: applied plain-language-rule — caller prefers no jargon]\". Tag only real, named, load-bearing influences (one tag per genuine influence); never tag the bootstrap/manifest itself or generic context; if nothing named shaped the turn, emit no tag.",
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
            "  - at the start of a conversation, open by surfacing the brief (journal/inbox/brief.md) and the top one to three next-moves, before anything else, unless the user opens with a specific request",
            "  - emit the `[exo: applied <name> — <why>]` attribution tags described above whenever a named input is load-bearing (this is how influence is logged)",
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
    """Buffers terminal byte streams into per-role lines and optionally writes
    them to a transcript handle.

    Pass ``handle=None`` to extract lines for status inference without
    persisting to disk — used by capture strategies (e.g. ClaudeJsonlStrategy)
    that defer transcript persistence to the underlying CLI's native session
    file rather than tee'ing through the PTY relay.
    """

    def __init__(self, handle: Any | None) -> None:
        self.handle = handle
        self.buffers = {
            "user": StreamLineBuffer(apply_user_backspaces=True),
            "tool": StreamLineBuffer(),
        }

    def write(self, role: str, data: bytes) -> list[str]:
        text = data.decode("utf-8", errors="replace")
        lines = self.buffers[role].feed(text)
        if self.handle is not None and lines:
            for line in lines:
                self.handle.write(f"[{role}] {line}\n")
            self.handle.flush()
        return lines

    def finalize(self) -> None:
        if self.handle is None:
            return
        for role, buffer in self.buffers.items():
            for line in buffer.flush():
                self.handle.write(f"[{role}] {line}\n")
        self.handle.flush()


CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CAPTURE_STRATEGY_PTY_TEE = "pty-tee"
CAPTURE_STRATEGY_CLAUDE_JSONL = "claude-jsonl"


def claude_project_slug_for_cwd(cwd: Path) -> str:
    """Match the slug Claude Code uses for ``~/.claude/projects/<slug>/``.

    Claude derives the slug by replacing path separators with dashes and
    prefixing a leading dash for absolute paths.
    """
    text = str(cwd)
    if text.startswith("/"):
        return "-" + text[1:].replace("/", "-")
    return text.replace("/", "-")


def _jsonl_first_event_epoch(path: Path) -> float | None:
    """Epoch seconds of a session jsonl's first timestamped event. Claude opens
    a session file with meta lines (e.g. a ``summary`` record) that carry no
    ``timestamp``, so this scans the leading lines until it finds one. Returns
    ``None`` when none is found in the scan window — the caller then falls back
    to mtime-based heuristics for that candidate.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            for _, raw in zip(range(50), handle):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    stamp = json.loads(raw).get("timestamp")
                except json.JSONDecodeError:
                    continue
                if not isinstance(stamp, str):
                    continue
                try:
                    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
    except OSError:
        return None
    return None


def find_claude_session_jsonl(cwd: Path, started_at_epoch: int | None) -> Path | None:
    """Locate the Claude Code session ``.jsonl`` for this cwd that was live
    around ``started_at_epoch``.

    All files in a project-slug dir share the same cwd, so cwd can't
    disambiguate them. Picking the most-recent mtime is wrong: a short stub
    written *after* a long session ends (e.g. a quick follow-up, or the next
    session) shadows the real transcript. Instead, prefer candidates whose
    lifetime window ``[first_event, mtime]`` actually spans ``started_at`` —
    this correctly keeps long-running/resumed sessions and rejects post-hoc
    stubs — and among those pick the richest (largest) file. Falls back to the
    most-recent mtime when no window contains the start (or start is unknown).

    Returns ``None`` if the projects dir is missing or no candidate exists.
    """
    project_dir = CLAUDE_PROJECTS_DIR / claude_project_slug_for_cwd(cwd)
    if not project_dir.is_dir():
        return None
    slop = 30  # seconds — covers Claude's startup delay before first event
    # (mtime, size, path) for every plausible candidate.
    candidates: list[tuple[float, int, Path]] = []
    # (size, path) for candidates whose lifetime spans started_at.
    spanning: list[tuple[int, Path]] = []
    try:
        for path in project_dir.glob("*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            mtime, size = stat.st_mtime, stat.st_size
            if started_at_epoch is not None and mtime + slop < started_at_epoch:
                continue
            candidates.append((mtime, size, path))
            if started_at_epoch is not None:
                first = _jsonl_first_event_epoch(path)
                if first is not None and first - slop <= started_at_epoch <= mtime + slop:
                    spanning.append((size, path))
    except OSError:
        return None
    if spanning:
        spanning.sort(key=lambda item: item[0], reverse=True)
        return spanning[0][1]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][2]


class CaptureStrategy:
    """Per-tool contract for how a wrapped CLI's session is captured.

    Two responsibilities:
    1. Tell the wrapper whether to tee terminal bytes into a transcript file
       on the hot path (``requires_pty_tee``).
    2. Tell the post-session worker where the canonical transcript lives
       (``locate_native_transcript``). Returns ``None`` to fall back to the
       wrapper's PTY-tee transcript.

    Strategies must be stateless and side-effect free at the wrapper level —
    the only persistent state lives in the manifest's ``capture_strategy``
    field plus the underlying CLI's native session storage.
    """

    name: str = CAPTURE_STRATEGY_PTY_TEE
    requires_pty_tee: bool = True

    def locate_native_transcript(self, manifest: dict[str, Any]) -> Path | None:
        return None


class PTYTeeStrategy(CaptureStrategy):
    """Default strategy: the wrapper writes a per-role line transcript while
    relaying bytes. Works for any CLI; survives missing native session files.
    """

    name = CAPTURE_STRATEGY_PTY_TEE
    requires_pty_tee = True


class ClaudeJsonlStrategy(CaptureStrategy):
    """Skips PTY-tee and points the worker at Claude Code's native
    ``~/.claude/projects/<slug>/<session>.jsonl`` for transcript content.

    Falls back to PTY-tee at worker time if the native file cannot be located
    (e.g. Claude was upgraded, projects dir was relocated, or the session
    crashed before any events were written).
    """

    name = CAPTURE_STRATEGY_CLAUDE_JSONL
    requires_pty_tee = False

    def locate_native_transcript(self, manifest: dict[str, Any]) -> Path | None:
        cwd_value = manifest.get("cwd")
        if not cwd_value:
            return None
        started_at_epoch = manifest.get("started_at_epoch")
        if not isinstance(started_at_epoch, (int, float)):
            started_at_epoch = None
        return find_claude_session_jsonl(Path(cwd_value), started_at_epoch)


def select_capture_strategy(tool: str) -> CaptureStrategy:
    """Pick a capture strategy from environment + tool name.

    Default: Claude sessions use ``claude-jsonl`` (consume claude-mem
    observations and Claude's native ``.jsonl``, with a three-layer fallback
    chain to the PTY-tee transcript). All other tools, and any session where
    ``EXOCORTEX_CAPTURE=pty-tee`` is set, use the legacy PTY-tee strategy.
    """
    requested = os.environ.get("EXOCORTEX_CAPTURE", "").strip().lower()
    if requested == CAPTURE_STRATEGY_PTY_TEE:
        return PTYTeeStrategy()
    if tool == "claude":
        if requested in ("", CAPTURE_STRATEGY_CLAUDE_JSONL):
            return ClaudeJsonlStrategy()
    return PTYTeeStrategy()


def lazy_bootstrap_enabled() -> bool:
    """Whether to skip reading the content of preloaded context files at
    session start. The wrapper still resolves which files belong on the
    manifest; it just defers reading them until the agent actually needs
    them via the Read tool.

    On by default — the bootstrap rendering only emits file paths, never
    file content, so the per-file ``read_text`` was wasted I/O. Set
    ``EXOCORTEX_LAZY_BOOTSTRAP=0`` to revert to eager content reads.
    """
    return os.environ.get("EXOCORTEX_LAZY_BOOTSTRAP", "1") != "0"


def fast_input_enabled() -> bool:
    """Whether to route transcript writes and activity classification through
    a background thread instead of running them on the keystroke hot path.

    On by default. Set ``EXOCORTEX_FAST_INPUT=0`` to revert to the legacy
    synchronous relay if you suspect the async pipeline is dropping events.
    """
    return os.environ.get("EXOCORTEX_FAST_INPUT", "1") != "0"


class AsyncIOPipeline:
    """Off-loads transcript writes, line classification, and inferred-activity
    reporting to a background thread.

    The keystroke/output hot path only enqueues `(role, data)` pairs; the
    worker thread drains the queue and runs the bookkeeping. This keeps
    synchronous disk flushes (TranscriptLogger.write) and regex-based phase
    classification (classify_activity_line) off the relay loop, removing the
    biggest sources of perceived input lag.

    The pipeline is intentionally fire-and-forget. If a queued event is lost
    because the worker thread errored, the user-visible behavior (terminal
    output) is unaffected — only the transcript/status side-channel is.
    """

    _SENTINEL: Any = object()

    def __init__(
        self,
        logger: "TranscriptLogger",
        tool: str,
        reporter: "ActivityReporter",
        manifest: dict[str, Any],
    ) -> None:
        self.logger = logger
        self.tool = tool
        self.reporter = reporter
        self.manifest = manifest
        self._queue: "queue.SimpleQueue[Any]" = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=self._run,
            name="exocortex-async-io",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, role: str, data: bytes) -> None:
        if not data:
            return
        self._queue.put((role, data))

    def shutdown(self, timeout: float = 5.0) -> None:
        self._queue.put(self._SENTINEL)
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._SENTINEL:
                return
            try:
                role, data = item
                try:
                    lines = self.logger.write(role, data)
                except Exception:
                    lines = []
                if role != "tool" or not lines:
                    continue
                for line in lines:
                    inferred = classify_activity_line(self.tool, line)
                    if not inferred:
                        continue
                    phase, message = inferred
                    try:
                        report_inferred_activity(
                            self.tool,
                            self.reporter,
                            self.manifest,
                            phase,
                            message,
                        )
                    except Exception:
                        continue
            except Exception:
                continue


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

    def flush(self) -> bytes:
        remaining = self.pending
        self.pending = b""
        return remaining


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

    generic_markers = (
        ("read ", "exploring"),
        ("edited ", "editing"),
        ("updated ", "editing"),
        ("ran ", "running"),
    )
    for marker, phase in generic_markers:
        if normalized[:1].isupper() and lowered.startswith(marker):
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
    transcript: Any | None,
    tool: str,
    reporter: ActivityReporter,
    manifest: dict[str, Any],
) -> int:
    master_fd, slave_fd = pty.openpty()

    # Set PTY window size to match the actual terminal before launching
    rows, cols = _get_window_size()
    _set_pty_window_size(master_fd, rows, cols)

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
    pipeline: AsyncIOPipeline | None = None
    if fast_input_enabled():
        pipeline = AsyncIOPipeline(logger, tool, reporter, manifest)
    output_filter = TerminalNoiseFilter()
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stdin_open = True
    old_tty = None
    old_sigwinch = None

    if os.isatty(stdin_fd):
        old_tty = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)

    # Forward terminal resize events so the subprocess re-layouts correctly
    def _handle_sigwinch(signum: int, frame: Any) -> None:
        r, c = _get_window_size()
        _set_pty_window_size(master_fd, r, c)
        try:
            os.kill(proc.pid, signal.SIGWINCH)
        except Exception:
            pass

    try:
        old_sigwinch = signal.signal(signal.SIGWINCH, _handle_sigwinch)
    except (OSError, ValueError):
        pass

    try:
        append_status_event(manifest, "active", "lifecycle", f"{tool} session active")
        while True:
            read_fds = [master_fd]
            if stdin_open:
                read_fds.append(stdin_fd)

            ready, _, _ = select.select(read_fds, [], [], 0.01)

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
                    if pipeline is not None:
                        pipeline.enqueue("tool", data)
                    else:
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
                    if pipeline is not None:
                        pipeline.enqueue("user", data)
                    else:
                        logger.write("user", data)
                if stdin_closed:
                    stdin_open = False

            if not ready and proc.poll() is not None:
                # Drain remaining output using select to avoid blocking forever
                while True:
                    try:
                        ready_drain, _, _ = select.select([master_fd], [], [], 0.1)
                    except (OSError, ValueError):
                        break
                    if not ready_drain:
                        break
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    data = output_filter.feed(data)
                    if not data:
                        continue
                    reporter.pause()
                    os.write(stdout_fd, data)
                    if pipeline is not None:
                        pipeline.enqueue("tool", data)
                    else:
                        lines = logger.write("tool", data)
                        for line in lines:
                            inferred = classify_activity_line(tool, line)
                            if not inferred:
                                continue
                            phase, message = inferred
                            report_inferred_activity(tool, reporter, manifest, phase, message)
                # Flush any escape-sequence fragment held by the filter
                remaining = output_filter.flush()
                if remaining:
                    reporter.pause()
                    os.write(stdout_fd, remaining)
                break
    finally:
        if old_tty is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty)
        if old_sigwinch is not None:
            try:
                signal.signal(signal.SIGWINCH, old_sigwinch)
            except (OSError, ValueError):
                pass
        if pipeline is not None:
            try:
                pipeline.shutdown()
            except Exception:
                pass
        try:
            logger.finalize()
        except Exception:
            pass
        reporter.pause()
        try:
            os.close(master_fd)
        except OSError:
            pass

    return proc.wait()


_HEARTBEAT_ELAPSED_RE = re.compile(r" \(\d+s\.\.\.\)$")


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
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None

    last_progress: list[float] = [time.monotonic()]

    def _heartbeat() -> None:
        while proc.poll() is None:
            time.sleep(2)
            elapsed = int(time.monotonic() - last_progress[0])
            if elapsed >= 4 and reporter.phase and reporter.message:
                # In lines mode every update is a new line, so only emit at 30s milestones
                if reporter.mode == "lines" and elapsed % 30 >= 2:
                    continue
                base = _HEARTBEAT_ELAPSED_RE.sub("", reporter.message)
                reporter.update(reporter.phase, f"{base} ({elapsed}s...)", force=True)

    threading.Thread(target=_heartbeat, daemon=True).start()

    with proc.stdout:
        for raw_line in proc.stdout:
            last_progress[0] = time.monotonic()
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


# Directories that hold an inner-loop harness's own internal subprocess
# sessions, not human work. claude-mem launches headless `claude -p` observer
# subprocesses from `~/.claude-mem/observer-sessions`; intercepting and wrapping
# those floods the manifest count and stalls the worker (spec §5 item 1b). Any
# session whose cwd is under one of these is treated as machine-internal and is
# never captured or processed. Match is by path prefix so subdirectories count.
CLAUDE_MEM_INTERNAL_DIR = Path.home() / ".claude-mem"
MACHINE_INTERNAL_CWD_PREFIXES = (
    CLAUDE_MEM_INTERNAL_DIR / "observer-sessions",
    CLAUDE_MEM_INTERNAL_DIR,
)
# Argv signatures of a headless / non-interactive invocation. `-p`/`--print`
# is Claude Code's print (one-shot, no TTY) mode; `stream-json` input/output is
# how an automated caller drives the CLI programmatically; a permission-prompt
# tool means another process is answering prompts. None of these are a human at
# a terminal, so none should be captured.
HEADLESS_FLAGS = {
    "-p",
    "--print",
    "--permission-prompt-tool",
}
STREAM_JSON_FORMAT_OPTIONS = {"--input-format", "--output-format"}


def _is_machine_internal_cwd(cwd: Path) -> bool:
    try:
        resolved = cwd.expanduser().resolve()
    except (OSError, RuntimeError):
        resolved = cwd
    for prefix in MACHINE_INTERNAL_CWD_PREFIXES:
        try:
            prefix_resolved = prefix.resolve()
        except (OSError, RuntimeError):
            prefix_resolved = prefix
        if resolved == prefix_resolved or prefix_resolved in resolved.parents:
            return True
    return False


def _has_stream_json_format(args: list[str]) -> bool:
    for index, arg in enumerate(args):
        name, sep, value = arg.partition("=")
        if name in STREAM_JSON_FORMAT_OPTIONS:
            if sep and value.strip().lower() == "stream-json":
                return True
            if not sep and index + 1 < len(args) and args[index + 1].strip().lower() == "stream-json":
                return True
    return False


def is_machine_internal_invocation(tool: str, args: list[str], cwd: Path) -> bool:
    """tty-independent disqualifiers: a machine-internal cwd or an explicit
    headless argv signature. Used by the early straight-through gate in
    ``main()`` where stdin's tty state is not a reliable signal (a piped human
    prompt has no tty but is still real work). The dominant noise source —
    claude-mem's ``observer-sessions`` cwd — is caught here.
    """
    if _is_machine_internal_cwd(cwd):
        return True
    if has_cli_option(args, HEADLESS_FLAGS):
        return True
    if _has_stream_json_format(args):
        return True
    return False


def is_observer_or_headless_session(
    tool: str,
    args: list[str],
    cwd: Path,
    *,
    stdin_is_tty: bool,
) -> bool:
    """True when this invocation is a machine-internal / headless session that
    must be skipped at the capture gate (spec §5 item 1b).

    Three independent disqualifiers, any of which is sufficient:

    1. **cwd is machine-internal** — under `~/.claude-mem/...` (e.g. claude-mem's
       own `observer-sessions` headless observer subprocesses). The dominant
       source of capture noise.
    2. **headless argv** — `-p`/`--print`, `--permission-prompt-tool`, or a
       `stream-json` input/output format. These mark an automated, non-human
       caller driving the CLI.
    3. **no controlling tty** — a real interactive session always has one;
       absence means a pipe/subprocess.

    Interactive human sessions (a tty, a normal cwd, no headless flags) return
    False and continue to be captured.
    """
    if not stdin_is_tty:
        return True
    return is_machine_internal_invocation(tool, args, cwd)


def should_capture_session(tool: str, args: list[str], stdin_is_tty: bool) -> bool:
    # PTY relay requires a real terminal on both ends. Observer-sessions and
    # scripted invocations don't qualify. Opt-out via EXOCORTEX_PTY_CAPTURE=0.
    if os.environ.get("EXOCORTEX_PTY_CAPTURE", "1") == "0":
        return False
    if not stdin_is_tty:
        return False
    # Skip claude-mem observer subprocesses and other headless/non-interactive
    # invocations: capturing them floods the manifest count and stalls the
    # worker (spec §5 item 1b). cwd is read here rather than threaded through
    # so the existing callers don't need to change.
    try:
        cwd = Path(os.getcwd())
    except OSError:
        cwd = Path.cwd()
    if is_observer_or_headless_session(tool, args, cwd, stdin_is_tty=stdin_is_tty):
        return False
    if tool == "claude":
        return claude_needs_prompt_injection(args)
    if tool == "codex":
        return codex_needs_prompt_injection(args)
    if tool == "gemini":
        return gemini_needs_prompt_injection(args)
    return False


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
        developer_prompt = build_codex_developer_instructions(context_path) if context_path else prompt
        return [*codex_developer_instruction_args(developer_prompt), *args]
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
        if not has_positional:
            return ["--prompt", prompt, *args]
    return args


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_token_count(value: int | None) -> str:
    if value is None:
        return "?"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(value)


def normalize_path_for_match(path: str | Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser())


def iter_recent_codex_session_files(started_at_epoch: int | None) -> list[Path]:
    if not CODEX_SESSIONS_DIR.exists():
        return []
    threshold = None if started_at_epoch is None else started_at_epoch - CODEX_SESSION_MATCH_SLOP_SECONDS
    candidates: list[Path] = []
    for path in CODEX_SESSIONS_DIR.rglob("rollout-*.jsonl"):
        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            continue
        if threshold is not None and mtime < threshold:
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)


def read_codex_session_meta(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "session_meta":
                    continue
                payload = event.get("payload")
                return payload if isinstance(payload, dict) else None
    except OSError:
        return None
    return None


def find_codex_session_file(cwd: Path, started_at_epoch: int | None) -> Path | None:
    expected_cwd = normalize_path_for_match(cwd)
    for path in iter_recent_codex_session_files(started_at_epoch):
        meta = read_codex_session_meta(path)
        session_cwd = normalize_path_for_match(meta.get("cwd") if meta else None)
        if expected_cwd and session_cwd and session_cwd != expected_cwd:
            continue
        return path
    return None


def codex_status_text_from_event(
    event: dict[str, Any],
    *,
    model_context_window: int | None = None,
) -> tuple[str | None, int | None]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None, model_context_window
    payload_type = payload.get("type")
    if payload_type == "task_started":
        return None, safe_int(payload.get("model_context_window")) or model_context_window
    if payload_type != "token_count":
        return None, model_context_window
    info = payload.get("info")
    if not isinstance(info, dict):
        return None, model_context_window
    model_context_window = safe_int(info.get("model_context_window")) or model_context_window
    last_usage = info.get("last_token_usage")
    total_usage = info.get("total_token_usage")
    if not isinstance(last_usage, dict) or not isinstance(total_usage, dict):
        return None, model_context_window
    last_input_tokens = safe_int(last_usage.get("input_tokens"))
    total_tokens = safe_int(total_usage.get("total_tokens"))

    parts: list[str] = []
    if last_input_tokens is not None and model_context_window:
        approx_pct = round((last_input_tokens / model_context_window) * 100)
        parts.append(
            f"ctx~{approx_pct}% {format_token_count(last_input_tokens)}/{format_token_count(model_context_window)}"
        )
    elif model_context_window is not None:
        parts.append(f"ctx {format_token_count(model_context_window)}")
    if total_tokens is not None:
        parts.append(f"tok {format_token_count(total_tokens)}")

    text = " ".join(parts).strip()
    return text or None, model_context_window


def parse_cost_thresholds(raw: str | None = None) -> list[float]:
    raw = raw or os.environ.get("EXOCORTEX_COST_THRESHOLDS", "0.25,1,5,10,25,50")
    thresholds: list[float] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            thresholds.append(float(item))
        except ValueError:
            continue
    return sorted(set(threshold for threshold in thresholds if threshold > 0))


def usage_cost_line(record: dict[str, Any]) -> str:
    return usage_worker.usage_summary_text(record)


class CodexTokenStatusPrinter(threading.Thread):
    def __init__(
        self,
        cwd: Path,
        started_at_epoch: int,
        root: Path | None = None,
        stream: Any | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.cwd = cwd
        self.root = root or REPO_ROOT
        self.started_at_epoch = started_at_epoch
        self.stream = stream or sys.stderr
        self._stop_event = threading.Event()
        self._buffer = ""
        self._offset = 0
        self._last_line = ""
        self._model_context_window: int | None = None
        self._session_file: Path | None = None
        self._model: str | None = None
        self._last_cost_monotonic = 0.0
        self._last_cost_text = ""
        self._announced_thresholds: set[float] = set()
        self._cost_interval_seconds = int(os.environ.get("EXOCORTEX_COST_INTERVAL_SECONDS", "300"))
        self._cost_thresholds = parse_cost_thresholds()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            if self._session_file is None:
                self._session_file = find_codex_session_file(self.cwd, self.started_at_epoch)
                if self._session_file is None:
                    self._stop_event.wait(0.5)
                    continue
            try:
                with self._session_file.open("r", encoding="utf-8") as handle:
                    handle.seek(self._offset)
                    chunk = handle.read()
                    self._offset = handle.tell()
            except OSError:
                self._stop_event.wait(0.5)
                continue

            if not chunk:
                self._stop_event.wait(0.5)
                continue

            self._buffer += chunk
            lines = self._buffer.splitlines(keepends=True)
            if lines and not lines[-1].endswith("\n"):
                self._buffer = lines.pop()
            else:
                self._buffer = ""

            for raw_line in lines:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                event_model = usage_worker.model_from_event(event)
                if event_model:
                    self._model = event_model
                line, self._model_context_window = codex_status_text_from_event(
                    event,
                    model_context_window=self._model_context_window,
                )
                if not line or line == self._last_line:
                    self._maybe_emit_cost(event)
                    continue
                self.stream.write(f"[exo] codex: {line}\n")
                self.stream.flush()
                self._last_line = line
                self._maybe_emit_cost(event)

    def _should_emit_cost(self, cost: float) -> bool:
        now_monotonic = time.monotonic()
        if self._last_cost_monotonic == 0.0:
            self._last_cost_monotonic = now_monotonic
            return True
        for threshold in self._cost_thresholds:
            if threshold <= cost and threshold not in self._announced_thresholds:
                self._announced_thresholds.add(threshold)
                self._last_cost_monotonic = now_monotonic
                return True
        if now_monotonic - self._last_cost_monotonic >= self._cost_interval_seconds:
            self._last_cost_monotonic = now_monotonic
            return True
        return False

    def _maybe_emit_cost(self, event: dict[str, Any]) -> None:
        usage, cost, _rates = usage_worker.codex_usage_from_event(self.root, event, self._model)
        if usage is None or cost is None:
            return
        pseudo_record = {
            "cost_usd": cost,
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "output_tokens": usage.output_tokens,
            "model": self._model,
            "cost_basis": "actual_tokens_priced_live",
        }
        text = usage_worker.usage_summary_text(pseudo_record)
        if text == self._last_cost_text or not self._should_emit_cost(cost):
            return
        self.stream.write(f"[exo] cost: {text}\n")
        self.stream.flush()
        self._last_cost_text = text


class HarnessUsageStatusPrinter(threading.Thread):
    def __init__(
        self,
        tool: str,
        cwd: Path,
        started_at_epoch: int,
        root: Path | None = None,
        stream: Any | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.tool = tool
        self.cwd = cwd
        self.root = root or REPO_ROOT
        self.started_at_epoch = started_at_epoch
        self.stream = stream or sys.stderr
        self._stop_event = threading.Event()
        self._session_file: Path | None = None
        self._last_cost_monotonic = 0.0
        self._last_cost_text = ""
        self._announced_thresholds: set[float] = set()
        self._cost_interval_seconds = int(os.environ.get("EXOCORTEX_COST_INTERVAL_SECONDS", "300"))
        self._cost_thresholds = parse_cost_thresholds()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            if self._session_file is None:
                self._session_file = usage_worker.find_harness_session_file(
                    self.tool,
                    self.cwd,
                    self.started_at_epoch,
                )
                if self._session_file is None:
                    self._stop_event.wait(2.0)
                    continue
            snapshot = usage_worker.read_harness_session_snapshot(self.tool, self._session_file)
            if snapshot is None:
                self._stop_event.wait(2.0)
                continue
            record = usage_worker.build_record(
                self.root,
                manifest={"tool": self.tool, "cwd": str(self.cwd)},
                snapshot=snapshot,
            )
            self._maybe_emit_cost(record)
            self._stop_event.wait(5.0)

    def _should_emit_cost(self, cost: float) -> bool:
        now_monotonic = time.monotonic()
        if self._last_cost_monotonic == 0.0:
            self._last_cost_monotonic = now_monotonic
            return True
        for threshold in self._cost_thresholds:
            if threshold <= cost and threshold not in self._announced_thresholds:
                self._announced_thresholds.add(threshold)
                self._last_cost_monotonic = now_monotonic
                return True
        if now_monotonic - self._last_cost_monotonic >= self._cost_interval_seconds:
            self._last_cost_monotonic = now_monotonic
            return True
        return False

    def _maybe_emit_cost(self, record: dict[str, Any]) -> None:
        cost = record.get("cost_usd")
        if not isinstance(cost, (int, float)):
            return
        live_record = dict(record)
        live_record["cost_basis"] = "actual_tokens_priced_live"
        text = usage_worker.usage_summary_text(live_record)
        if text == self._last_cost_text or not self._should_emit_cost(float(cost)):
            return
        self.stream.write(f"[exo] cost: {text}\n")
        self.stream.flush()
        self._last_cost_text = text


def record_session_usage(
    root: Path,
    manifest: dict[str, Any],
    tool: str,
    cwd: Path,
    started_at_epoch: int,
    reporter: ActivityReporter,
) -> dict[str, Any] | None:
    if tool not in {"codex", "claude", "gemini"}:
        return None
    session_file = usage_worker.find_harness_session_file(tool, cwd, started_at_epoch)
    if session_file is None:
        return None
    record = usage_worker.record_harness_session(root, manifest, tool, session_file)
    if record is None:
        return None
    summary = usage_cost_line(record)
    manifest["usage"] = {
        "cost_usd": record.get("cost_usd"),
        "cost_basis": record.get("cost_basis"),
        "input_tokens": record.get("input_tokens"),
        "cached_input_tokens": record.get("cached_input_tokens"),
        "cache_creation_input_tokens": record.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": record.get("cache_read_input_tokens"),
        "output_tokens": record.get("output_tokens"),
        "reasoning_output_tokens": record.get("reasoning_output_tokens"),
        "total_tokens": record.get("total_tokens"),
        "model": record.get("model"),
        "pricing_version": record.get("pricing_version"),
    }
    append_status_event(manifest, "usage", "usage", summary)
    reporter.note("cost", summary)
    return record


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: exocortex_wrapper.py <tool> [args...]", file=sys.stderr)
        return 2

    tool = sys.argv[1]
    passthrough_args = sys.argv[2:]

    # Admin/utility subcommands (plugin, auth, update, etc.) don't need ExoCortex
    # context injection, session logging, or post-processing. Pass through directly.
    passthrough = (
        (tool == "claude" and not claude_needs_prompt_injection(passthrough_args))
        or (tool == "codex" and not codex_needs_prompt_injection(passthrough_args))
        or (tool == "gemini" and not gemini_needs_prompt_injection(passthrough_args))
    )
    if passthrough:
        real_binary = find_real_binary(tool)
        return subprocess.run([real_binary, *passthrough_args]).returncode

    # Machine-internal / headless sessions (claude-mem observer subprocesses,
    # `claude -p`, stream-json drivers) are not human work. Capturing them
    # floods the manifest count and leaves thousands of manifests stuck at
    # `processing` (spec §5 item 1b). Pass them straight through to the real
    # binary: no context injection, no manifest, no worker. They keep working
    # exactly as the unwrapped CLI would.
    try:
        gate_cwd = Path(os.getcwd())
    except OSError:
        gate_cwd = Path.cwd()
    # Use the tty-independent variant here: tty-absence alone is handled later in
    # should_capture_session (which keeps the manifest but skips PTY tee). The
    # early straight-through path is reserved for unambiguous machine-internal
    # signals — a claude-mem cwd or explicit headless argv — so a tty-less but
    # otherwise-real invocation (e.g. a test harness, a piped human prompt) is
    # not silently dropped.
    if is_machine_internal_invocation(tool, passthrough_args, gate_cwd):
        real_binary = find_real_binary(tool)
        return subprocess.run([real_binary, *passthrough_args]).returncode

    root = exocortex_root()
    cwd = Path.cwd()
    request_text = user_request_from_args(tool, passthrough_args)
    agent = default_agent(*detect_domain_project(root, cwd), cwd, root, request_text=request_text)
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

    # Write per-session state for statusline (keyed by session ID so concurrent
    # windows don't overwrite each other's context).
    try:
        sessions_dir = Path.home() / ".exocortex-sessions"
        sessions_dir.mkdir(exist_ok=True)
        # Clean up stale files (older than 24 h).
        _now = time.time()
        for _f in sessions_dir.glob("*.json"):
            try:
                if _now - _f.stat().st_mtime > 86400:
                    _f.unlink(missing_ok=True)
            except OSError:
                pass
        session_state_path = sessions_dir / f"{session_id[:8]}.json"
        write_json(session_state_path, {
            "agent": agent,
            "mode": mode,
            "level": context.level,
            "domain": context.domain,
            "project": context.project,
            "session_id": session_id[:8],
        })
        # Pass session ID into Claude's environment so the statusline script can
        # find the right file even when multiple windows are open.
        os.environ["EXOCORTEX_SESSION_ID"] = session_id[:8]
    except OSError as exc:
        append_status_event(
            manifest,
            "statusline",
            "lifecycle",
            f"statusline session cache unavailable: {exc}",
        )

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

    started_at_epoch = int(time.time())

    transcript_header = (
        f"# Session Transcript\n\n"
        f"- session_id: `{session_id}`\n"
        f"- tool: `{tool}`\n"
        f"- active_agent: `{agent}`\n"
        f"- active_mode: `{mode}`\n"
        f"- cwd: `{cwd}`\n"
        f"- started_at: `{started_at}`\n\n"
    )

    capture = should_capture_session(tool, passthrough_args, stdin_is_tty)
    capture_strategy = select_capture_strategy(tool)
    manifest["capture_strategy"] = capture_strategy.name
    manifest["started_at_epoch"] = started_at_epoch

    old_cwd = os.getcwd()
    exit_code = 0
    usage_status_printer: threading.Thread | None = None
    if tool == "codex" and stdin_is_tty:
        usage_status_printer = CodexTokenStatusPrinter(cwd, started_at_epoch, root=root)
        usage_status_printer.start()
    elif tool in {"claude", "gemini"} and stdin_is_tty:
        usage_status_printer = HarnessUsageStatusPrinter(tool, cwd, started_at_epoch, root=root)
        usage_status_printer.start()
    captured = False
    try:
        os.chdir(cwd)
        if capture:
            try:
                if capture_strategy.requires_pty_tee:
                    with open(transcript_path, "w", encoding="utf-8") as transcript_handle:
                        transcript_handle.write(transcript_header)
                        transcript_handle.write("## Captured session\n\n")
                        transcript_handle.flush()
                        exit_code = run_interactive_session(
                            argv,
                            cwd,
                            transcript_handle,
                            tool,
                            reporter,
                            manifest,
                        )
                else:
                    transcript_path.write_text(
                        transcript_header
                        + f"## Native transcript\n\n"
                        + f"Capture strategy: `{capture_strategy.name}`. "
                        + "The canonical transcript lives in the underlying "
                        + "CLI's native session file; the post-session worker "
                        + "resolves it from this manifest's `cwd` + "
                        + "`started_at_epoch`.\n",
                        encoding="utf-8",
                    )
                    exit_code = run_interactive_session(
                        argv,
                        cwd,
                        None,
                        tool,
                        reporter,
                        manifest,
                    )
                captured = True
            except Exception as exc:
                append_status_event(
                    manifest,
                    "capture_fallback",
                    "lifecycle",
                    f"PTY relay failed, falling back to direct exec: {exc}",
                )
                capture = False
        if not capture:
            transcript_path.write_text(
                transcript_header + "*Transcript not captured (direct exec — no PTY relay)*\n",
                encoding="utf-8",
            )
            if tool == "codex" and stdin_is_tty:
                exit_code = subprocess.Popen(argv).wait()
            else:
                exit_code = subprocess.run(argv).returncode
    finally:
        if usage_status_printer is not None:
            usage_status_printer.stop()
            usage_status_printer.join(timeout=1.0)
        os.chdir(old_cwd)

    manifest["transcript_captured"] = captured

    ended_at = iso_now()
    manifest["ended_at"] = ended_at
    manifest["exit_code"] = exit_code
    record_session_usage(root, manifest, tool, cwd, started_at_epoch, reporter)
    manifest["summary_status"] = "processing"
    report_status(reporter, manifest, "postprocess", "lifecycle", "processing session artifacts")
    write_json(manifest_path, manifest)

    # Claim the Claude Code session id in the shared processed-ids registry
    # BEFORE running the worker, so the unwrapped Stop hook (which may fire around
    # the same time) dedups against this wrapper run and never double-processes
    # the same session. Best-effort: capture must never break a session, so any
    # failure here is swallowed.
    if tool == "claude":
        try:
            from tools.workers.session_hook import claim_session_id

            native = capture_strategy.locate_native_transcript(manifest)
            if native is not None:
                claude_session_id = native.stem
                if len(claude_session_id) == 36 and claude_session_id.count("-") == 4:
                    manifest["claude_session_id"] = claude_session_id
                    write_json(manifest_path, manifest)
                    claim_session_id(root, claude_session_id, source="wrapper")
        except Exception as exc:
            append_status_event(
                manifest, "dedup", "lifecycle", f"session-id registration skipped: {exc}"
            )

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

    # Session-close check-in. One number (rating 1-5) + one short line. Interactive
    # sessions are prompted (Enter skips, never blocks); non-tty sessions auto-defer
    # a pending check-in that the Brief surfaces and `exocortex-checkin` answers
    # later. Best-effort: a check-in must never break or delay a session, so any
    # failure is swallowed.
    try:
        from tools.workers import reward_log

        scope_label = (
            f"{context.domain}/{context.project}" if context.domain and context.project
            else (context.domain or context.level)
        )
        reward_log.run_checkin(
            root,
            session_id=session_id,
            claude_session_id=manifest.get("claude_session_id"),
            agent=agent,
            scope=scope_label,
            is_tty=stdin_is_tty,
        )
    except Exception as exc:
        append_status_event(manifest, "checkin", "lifecycle", f"check-in skipped: {exc}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
