#!/usr/bin/env python3
"""Stop-hook entrypoint: give unwrapped Claude Code sessions the same synthesis
pass the terminal wrapper runs at session end.

The terminal wrapper (`tools/wrappers/exocortex_wrapper.py`) only runs for
sessions launched through the `claude`/`codex`/`gemini` shell shims. Sessions
started any other way — the desktop app, the web UI, an IDE integration, or a
plain `claude` on a machine where the shim is not installed — never reach
`process_session.py`, so they produce no summary or promotion candidates. This
module closes that gap: wired as a Claude Code `Stop` hook, it reads the hook
payload from stdin, synthesizes a session manifest, and runs the same worker.

Two invariants (spec §5 Capture):

1. **Dedup by Claude Code session id.** The wrapper records every Claude
   session id it processes into a shared registry. The Stop hook claims a
   session id atomically before doing any work; if the wrapper already handled
   that id (or another Stop-hook invocation did), the hook is a no-op. This is
   what stops the wrapper path and the Stop hook from double-processing one
   session.

2. **Capture must never break a session.** Every code path here is wrapped so
   that any failure exits 0 with an empty (continue) response. A hook crash can
   never kill or stall the inner-loop session.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Registry of Claude Code session ids that have already been synthesized, by
# either the wrapper path or a prior Stop-hook invocation. Lives under the
# journal so it travels with the session artifacts and is easy to inspect.
PROCESSED_REGISTRY_REL = Path("journal") / "sessions" / ".processed-ids.json"
# Cap the registry so it cannot grow without bound; newest ids are kept.
MAX_REGISTRY_IDS = 5000


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _registry_path(root: Path) -> Path:
    return root / PROCESSED_REGISTRY_REL


def _read_registry(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ids": [], "seen": {}}
    if not isinstance(data, dict):
        return {"ids": [], "seen": {}}
    data.setdefault("ids", [])
    data.setdefault("seen", {})
    return data


def claim_session_id(root: Path, session_id: str, *, source: str) -> bool:
    """Atomically record ``session_id`` as processed.

    Returns ``True`` if this call claimed the id (it was not present before),
    ``False`` if it was already claimed. Both the wrapper and the Stop hook call
    this; the loser of the race must not process the session. Uses an exclusive
    file lock so concurrent wrapper/hook invocations cannot both win.
    """
    import fcntl

    path = _registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            raw = handle.read()
            try:
                data = json.loads(raw) if raw.strip() else {"ids": [], "seen": {}}
            except json.JSONDecodeError:
                data = {"ids": [], "seen": {}}
            if not isinstance(data, dict):
                data = {"ids": [], "seen": {}}
            ids = data.setdefault("ids", [])
            seen = data.setdefault("seen", {})
            if session_id in seen:
                return False
            ids.append(session_id)
            seen[session_id] = {"source": source, "at": iso_now()}
            if len(ids) > MAX_REGISTRY_IDS:
                dropped = ids[:-MAX_REGISTRY_IDS]
                del ids[:-MAX_REGISTRY_IDS]
                for old in dropped:
                    seen.pop(old, None)
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            return True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def is_session_processed(root: Path, session_id: str) -> bool:
    data = _read_registry(_registry_path(root))
    return session_id in data.get("seen", {})


_EXOCORTEX_SUMMARIZER_MARKERS = (
    "You are the ExoCortex session summarizer",
    "You are the ExoCortex period synthesizer",
)


def _first_user_event_meta(transcript_path: Path) -> dict[str, Any]:
    """Best-effort read of the first user turn in a Claude Code session jsonl."""
    try:
        with transcript_path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "user":
                    continue
                message = event.get("message")
                content = ""
                if isinstance(message, dict):
                    raw = message.get("content", "")
                    if isinstance(raw, str):
                        content = raw
                    elif isinstance(raw, list):
                        content = " ".join(
                            str(block.get("text", ""))
                            for block in raw
                            if isinstance(block, dict) and block.get("type") == "text"
                        )
                return {
                    "entrypoint": str(event.get("entrypoint") or ""),
                    "prompt_source": str(event.get("promptSource") or ""),
                    "content": content,
                }
    except OSError:
        pass
    return {}


def _is_headless_from_transcript(payload: dict[str, Any]) -> bool:
    """Detect headless ``claude -p`` sessions the Stop payload cannot see via argv.

    Print-mode sessions record ``entrypoint: sdk-cli`` (interactive sessions use
    ``cli``). ExoCortex-owned summarizer prompts also carry a fixed header.
    """
    transcript_value = payload.get("transcript_path") or ""
    if not transcript_value:
        return False
    path = Path(str(transcript_value)).expanduser()
    if not path.is_file():
        return False
    meta = _first_user_event_meta(path)
    if meta.get("entrypoint") == "sdk-cli":
        return True
    content = meta.get("content") or ""
    return any(marker in content for marker in _EXOCORTEX_SUMMARIZER_MARKERS)


def _is_observer_or_headless_payload(payload: dict[str, Any]) -> bool:
    """Whether a Stop-hook payload describes a machine-internal / headless
    session that must be skipped (spec §5 item 1b).

    The payload carries no argv, so cwd and the native transcript are the
    available signals. Delegates cwd/headless-flag detection to the wrapper's
    detector (single source of truth) and additionally inspects the session
    jsonl for print-mode (``sdk-cli``) and ExoCortex summarizer prompts.
    """
    if _is_headless_from_transcript(payload):
        return True
    cwd_value = payload.get("cwd")
    if not cwd_value:
        return False
    try:
        from tools.wrappers.exocortex_wrapper import is_observer_or_headless_session

        return is_observer_or_headless_session(
            "claude", [], Path(cwd_value), stdin_is_tty=True
        )
    except Exception:
        # If the wrapper detector can't be imported, fall back to a conservative
        # inline check on the claude-mem internal path so the filter still works.
        try:
            resolved = Path(cwd_value).expanduser().resolve()
            internal = (Path.home() / ".claude-mem").resolve()
            return resolved == internal or internal in resolved.parents
        except (OSError, RuntimeError):
            return False


def _started_at_epoch_from_transcript(transcript_path: Path) -> int | None:
    """First-event epoch of a Claude Code session jsonl, used to help the worker
    locate the native transcript. Reuses the wrapper's scanner."""
    try:
        from tools.wrappers.exocortex_wrapper import _jsonl_first_event_epoch
    except Exception:
        return None
    epoch = _jsonl_first_event_epoch(transcript_path)
    return int(epoch) if epoch is not None else None


_EXO_TAG_RE = re.compile(r"\[exo:\s*(?:applied\s+)?(.+?)\]", re.IGNORECASE | re.DOTALL)


def _assistant_text_from_jsonl(transcript_path: Path) -> str:
    """Concatenate every assistant text block from a Claude Code session jsonl.

    Best-effort and fail-open: a malformed line is skipped, not fatal. Used only
    to harvest the `[exo: applied …]` attribution tags the model emits inline."""
    chunks: list[str] = []
    try:
        with transcript_path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"assistant"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                message = event.get("message") if isinstance(event, dict) else None
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    chunks.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            chunks.append(str(block.get("text", "")))
    except OSError:
        return ""
    return "\n".join(chunks)


def scan_influence_tags(transcript_path: Path | None) -> list[str]:
    """The `[exo: applied <name> — <why>]` tags the model left in its replies.

    This is the action/attribution signal: which named ExoCortex inputs actually
    changed an answer this session. Returned as cleaned, de-duplicated inner
    strings (order preserved) so the manifest and reward log record real usage,
    not just what was injected."""
    if transcript_path is None or not transcript_path.exists():
        return []
    text = _assistant_text_from_jsonl(transcript_path)
    if not text:
        return []
    seen: dict[str, None] = {}
    for raw in _EXO_TAG_RE.findall(text):
        tag = " ".join(raw.split()).strip()
        if not tag or tag.lower() == "applied" or tag in seen:
            continue
        # Reject placeholders/examples (`<name>`, `…`) and anything with no
        # actual word — a real tag names a memory/rule/brief item.
        if "<" in tag or ">" in tag or not re.search(r"\w", tag.replace("…", "")):
            continue
        seen[tag] = None
    return list(seen)


def _resolve_context(root: Path, cwd: Path) -> Any | None:
    try:
        from tools.wrappers import exocortex_wrapper as wrapper

        domain, project = wrapper.detect_domain_project(root, cwd)
        agent = wrapper.default_agent(domain, project, cwd, root)
        mode = wrapper.default_mode(agent)
        return wrapper.collect_context(root, cwd, agent, mode)
    except Exception:
        return None


def build_manifest_from_hook(
    root: Path,
    payload: dict[str, Any],
    *,
    session_dir: Path,
    started_at: str,
) -> dict[str, Any]:
    """Synthesize a manifest equivalent to the wrapper's, from a Stop-hook
    payload. ``session_id`` is the local artifact id (a fresh uuid); the Claude
    Code session id is recorded separately as ``claude_session_id`` and is what
    dedup keys on.
    """
    claude_session_id = str(payload.get("session_id") or "")
    cwd_value = payload.get("cwd") or str(Path.cwd())
    cwd = Path(cwd_value)
    transcript_value = payload.get("transcript_path") or ""
    transcript_native = Path(transcript_value).expanduser() if transcript_value else None

    local_id = str(uuid.uuid4())
    transcript_path = session_dir / f"{local_id}.transcript.md"
    context_path = session_dir / f"{local_id}.context.md"
    summary_path = session_dir / f"{local_id}.summary.md"
    candidates_path = session_dir / f"{local_id}.candidates.md"

    context = _resolve_context(root, cwd)
    if context is not None:
        agent = context.active_agent
        mode = context.active_mode
        domain = context.domain
        project = context.project
        level = context.level
        health_snapshot = context.health_snapshot
        try:
            from tools.wrappers import exocortex_wrapper as wrapper

            context_path.parent.mkdir(parents=True, exist_ok=True)
            context_path.write_text(wrapper.build_context_prompt(context) + "\n", encoding="utf-8")
        except Exception:
            pass
    else:
        agent = "chief-of-staff"
        mode = "conversation"
        domain = None
        project = None
        level = "root"
        health_snapshot = {}

    started_at_epoch = None
    if transcript_native is not None and transcript_native.exists():
        started_at_epoch = _started_at_epoch_from_transcript(transcript_native)

    manifest: dict[str, Any] = {
        "session_id": local_id,
        "claude_session_id": claude_session_id,
        "tool": "claude",
        "source": "stop-hook",
        "argv": [],
        "cwd": str(cwd),
        "root": str(root),
        "started_at": started_at,
        "started_at_epoch": started_at_epoch,
        "ended_at": iso_now(),
        "exit_code": 0,
        "active_agent": agent,
        "active_mode": mode,
        "domain": domain,
        "project": project,
        "level": level,
        "health_snapshot": health_snapshot,
        "transcript_path": str(transcript_path.relative_to(root)),
        "context_path": str(context_path.relative_to(root)),
        "summary_path": str(summary_path.relative_to(root)),
        "candidates_path": str(candidates_path.relative_to(root)),
        # The Stop hook always uses Claude's native session jsonl as the
        # transcript source; there is no PTY-tee to read from.
        "capture_strategy": "claude-jsonl",
        "transcript_captured": False,
        "summary_status": "processing",
        "status_events": [],
        # Which named ExoCortex inputs the model said it actually applied this
        # session (the inline `[exo: applied …]` tags). The action signal.
        "influence_tags": scan_influence_tags(transcript_native),
    }

    # Leave a small placeholder transcript file so the daily-journal step has
    # something to read; the worker prefers the native jsonl regardless.
    try:
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(
            "# Session Transcript\n\n"
            f"- session_id: `{local_id}`\n"
            f"- claude_session_id: `{claude_session_id}`\n"
            f"- source: `stop-hook`\n"
            f"- cwd: `{cwd}`\n"
            f"- started_at: `{started_at}`\n\n"
            "## Native transcript\n\n"
            "Captured outside the terminal wrapper (Stop hook). The canonical "
            "transcript lives in Claude Code's native session file; the worker "
            "resolves it from this manifest's `cwd` + `started_at_epoch`.\n",
            encoding="utf-8",
        )
    except OSError:
        pass

    return manifest


def run_worker(root: Path, manifest_path: Path) -> int:
    """Dispatch the postprocess worker as a *detached* background process and
    return immediately. Returns 0 on successful dispatch, non-zero otherwise.

    The worker (process_session.py) runs a long, serial, LLM-heavy chain
    (summary, period synthesis, rollups, project-state, Brief). The old design
    ran it synchronously under a 180s timeout, so any real working session was
    killed mid-flight — freezing summaries, candidates, and the Brief together
    (the "stillnobrief" failure). Detaching it (own session/process group, no
    controlling tty, stdin closed, output to a log) lets it always run to
    completion no matter how long it takes. This is the harness-agnostic version
    of what makes claude-mem seamless: hooks enqueue, a background worker
    processes out-of-band, never under a hook timeout."""
    command = [sys.executable, str(root / "tools" / "workers" / "process_session.py"), str(manifest_path)]
    log_dir = root / "journal" / "logs"
    log_handle: Any = subprocess.DEVNULL
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_dir / "worker.log", "ab")
    except OSError:
        log_handle = subprocess.DEVNULL
    try:
        subprocess.Popen(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            close_fds=True,
        )
        return 0
    except Exception:
        return 1
    finally:
        if log_handle is not subprocess.DEVNULL:
            try:
                log_handle.close()
            except Exception:
                pass


def refresh_brief(root: Path) -> bool:
    """Rebuild the startup Brief from current state. Sub-second and fail-open.

    The worker (process_session.py) already refreshes the Brief as its final
    step — but that step is the tail of a long, LLM-heavy chain run under
    run_worker's timeout. On any real working session the chain can exceed the
    timeout and be killed before reaching the Brief refresh, leaving the next
    session to open on a stale Brief (the original "still no brief" failure).
    Calling this here, outside the killable worker, guarantees the Brief is
    current at session close regardless of whether the heavy postprocess
    finished. Never raises: a hook step must not break the session."""
    try:
        from tools.workers import build_brief

        build_brief.write_brief(root)
        return True
    except Exception:
        return False


def process_stop_hook(payload: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    """Core logic. Returns a small result dict describing what happened
    (``status`` is one of ``processed``/``skipped``/``noop``/``error``).

    Never raises: the entrypoint relies on this returning rather than throwing
    so that a Stop-hook failure can never break the session.
    """
    try:
        if root is None:
            from tools.wrappers.exocortex_wrapper import exocortex_root

            root = exocortex_root()
        root = Path(root).resolve()

        claude_session_id = str(payload.get("session_id") or "").strip()
        if not claude_session_id:
            return {"status": "noop", "reason": "no session_id in payload"}

        # Skip claude-mem observer subprocesses and other machine-internal /
        # headless sessions (spec §5 item 1b). The Stop payload carries no argv,
        # so the available signal is the session cwd: anything under
        # `~/.claude-mem/...` is the inner-loop harness talking to itself, not
        # human work. Processing it floods the manifest count and stalls the
        # worker. Do this BEFORE claiming the id so the registry is not polluted
        # with observer ids.
        if _is_observer_or_headless_payload(payload):
            return {
                "status": "skipped",
                "reason": "observer/headless session",
                "session_id": claude_session_id,
            }

        # Dedup: claim the id. If we lose the race (wrapper or a prior hook
        # already processed it), do nothing.
        if not claim_session_id(root, claude_session_id, source="stop-hook"):
            return {"status": "skipped", "reason": "already processed", "session_id": claude_session_id}

        started_at = iso_now()
        date_str = started_at[:10]
        session_dir = root / "journal" / "sessions" / date_str
        session_dir.mkdir(parents=True, exist_ok=True)

        manifest = build_manifest_from_hook(
            root, payload, session_dir=session_dir, started_at=started_at
        )
        manifest_path = session_dir / f"{manifest['session_id']}.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        # Log the attribution signal: which named inputs the session applied.
        tags = manifest.get("influence_tags") or []
        if tags:
            try:
                from tools.workers import reward_log

                reward_log.append_reward(
                    root,
                    session_id=manifest["session_id"],
                    claude_session_id=manifest.get("claude_session_id") or None,
                    agent=manifest.get("active_agent"),
                    scope=manifest.get("level"),
                    energy=None,
                    juice=None,
                    source="influence-tags",
                    influence_tags=tags,
                )
            except Exception:
                pass

        code = run_worker(root, manifest_path)

        # Render the Brief synchronously here. It is cheap (well under a second
        # now that the hygiene scan no longer walks the journal or nested project
        # repos), so the next session is guaranteed to open on a current Brief
        # without waiting on the detached worker's long synthesis. The worker
        # also refreshes it at the end of its run, upgrading it with this
        # session's freshly synthesised data once that finishes in the
        # background. Both writes are atomic, so they never race to an empty file.
        brief_refreshed = refresh_brief(root)
        return {
            "status": "dispatched" if code == 0 else "error",
            "session_id": claude_session_id,
            "local_id": manifest["session_id"],
            "worker_code": code,
            "brief_refreshed": brief_refreshed,
        }
    except Exception as exc:  # never let a hook failure kill a session
        return {"status": "error", "reason": str(exc)}


def main() -> int:
    """Stop-hook entrypoint. Always exits 0: a Stop hook that returns non-zero
    can interrupt the session, so capture must fail open."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    result = process_stop_hook(payload)

    # Stop hooks may emit JSON on stdout to control the session; emit a benign
    # continue response and keep our own status on stderr for debugging.
    try:
        print(json.dumps({"continue": True, "suppressOutput": True}))
        if result.get("status") not in {"dispatched", "skipped", "noop"}:
            print(f"[exo] session_hook: {result}", file=sys.stderr)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
