#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workers import usage as usage_worker


CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
STATUS_CACHE_DIR = Path.home() / ".exocortex-codex-status"
DEFAULT_MAX_AGE_SECONDS = 12 * 60 * 60
SESSION_MATCH_SLOP_SECONDS = 10


@dataclass
class SessionSnapshot:
    session_file: str
    session_id: str | None
    cwd: str | None
    model: str | None
    model_provider: str | None
    model_context_window: int | None
    last_input_tokens: int | None
    last_cached_input_tokens: int | None
    last_output_tokens: int | None
    total_input_tokens: int | None
    total_cached_input_tokens: int | None
    total_output_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    cost_basis: str | None


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_tokens(value: int | None) -> str:
    if value is None:
        return "?"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(value)


def tty_key(tty_path: str | None) -> str:
    if not tty_path:
        return "default"
    cleaned = tty_path.strip()
    if not cleaned or cleaned == "not a tty":
        return "default"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", cleaned.strip("/"))


def cache_path_for_tty(tty_path: str | None) -> Path:
    return STATUS_CACHE_DIR / f"{tty_key(tty_path)}.json"


def normalize_path(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser())


def cwd_matches(current_cwd: str | None, recorded_cwd: str | None) -> bool:
    current = normalize_path(current_cwd)
    recorded = normalize_path(recorded_cwd)
    if not current or not recorded:
        return True
    return current.startswith(recorded) or recorded.startswith(current)


def iter_recent_session_files(started_at: int | None) -> list[Path]:
    if not CODEX_SESSIONS_DIR.exists():
        return []
    threshold = None if started_at is None else started_at - SESSION_MATCH_SLOP_SECONDS
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


def read_first_session_meta(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "session_meta":
                    continue
                payload = event.get("payload")
                if isinstance(payload, dict):
                    return payload
                return None
    except OSError:
        return None
    return None


def find_session_file(cwd: str | None, started_at: int | None) -> Path | None:
    expected_cwd = normalize_path(cwd)
    for path in iter_recent_session_files(started_at):
        meta = read_first_session_meta(path)
        session_cwd = normalize_path(meta.get("cwd") if meta else None)
        if expected_cwd and session_cwd and session_cwd != expected_cwd:
            continue
        return path
    return None


def parse_snapshot(path: Path) -> SessionSnapshot | None:
    session_id: str | None = None
    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    model_context_window: int | None = None
    last_input_tokens: int | None = None
    last_cached_input_tokens: int | None = None
    last_output_tokens: int | None = None
    total_input_tokens: int | None = None
    total_cached_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_tokens: int | None = None

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
                event_type = event.get("type")
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if event_type == "session_meta":
                    session_id = payload.get("id") if isinstance(payload.get("id"), str) else session_id
                    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else cwd
                    model_provider = payload.get("model_provider") if isinstance(payload.get("model_provider"), str) else model_provider
                event_model = usage_worker.model_from_event(event)
                if event_model:
                    model = event_model
                payload_type = payload.get("type")
                if payload_type == "task_started":
                    model_context_window = safe_int(payload.get("model_context_window")) or model_context_window
                if payload_type == "token_count":
                    info = payload.get("info")
                    if not isinstance(info, dict):
                        continue
                    model_context_window = safe_int(info.get("model_context_window")) or model_context_window
                    last_usage = info.get("last_token_usage")
                    if isinstance(last_usage, dict):
                        last_input_tokens = safe_int(last_usage.get("input_tokens"))
                        last_cached_input_tokens = safe_int(last_usage.get("cached_input_tokens"))
                        last_output_tokens = safe_int(last_usage.get("output_tokens"))
                    total_usage = info.get("total_token_usage")
                    if isinstance(total_usage, dict):
                        total_input_tokens = safe_int(total_usage.get("input_tokens"))
                        total_cached_input_tokens = safe_int(total_usage.get("cached_input_tokens"))
                        total_output_tokens = safe_int(total_usage.get("output_tokens"))
                        total_tokens = safe_int(total_usage.get("total_tokens"))
    except OSError:
        return None

    usage = usage_worker.TokenUsage(
        input_tokens=total_input_tokens or 0,
        cached_input_tokens=total_cached_input_tokens or 0,
        output_tokens=total_output_tokens or 0,
        total_tokens=total_tokens or 0,
        model_context_window=model_context_window,
    )
    pricing = usage_worker.load_rates(REPO_ROOT)
    cost = usage_worker.cost_usd(usage, usage_worker.model_rates(pricing, model))
    return SessionSnapshot(
        session_file=str(path),
        session_id=session_id,
        cwd=cwd,
        model=model,
        model_provider=model_provider,
        model_context_window=model_context_window,
        last_input_tokens=last_input_tokens,
        last_cached_input_tokens=last_cached_input_tokens,
        last_output_tokens=last_output_tokens,
        total_input_tokens=total_input_tokens,
        total_cached_input_tokens=total_cached_input_tokens,
        total_output_tokens=total_output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost,
        cost_basis="actual_tokens_priced" if cost is not None else None,
    )


def build_summary_text(snapshot: SessionSnapshot) -> str:
    parts: list[str] = []
    if snapshot.last_input_tokens is not None and snapshot.model_context_window:
        approx_pct = round((snapshot.last_input_tokens / snapshot.model_context_window) * 100)
        parts.append(
            f"ctx~{approx_pct}% {format_tokens(snapshot.last_input_tokens)}/{format_tokens(snapshot.model_context_window)}"
        )
    elif snapshot.model_context_window is not None:
        parts.append(f"ctx {format_tokens(snapshot.model_context_window)}")
    if snapshot.total_tokens is not None:
        parts.append(f"tok {format_tokens(snapshot.total_tokens)}")
    elif snapshot.total_input_tokens is not None:
        parts.append(f"in {format_tokens(snapshot.total_input_tokens)}")
    if snapshot.cost_usd is not None:
        parts.append(usage_worker.format_usd(snapshot.cost_usd))
    return " ".join(parts)


def write_cache(tty_path: str | None, snapshot: SessionSnapshot) -> Path:
    STATUS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path_for_tty(tty_path)
    payload = asdict(snapshot)
    payload["summary_text"] = build_summary_text(snapshot)
    payload["tty"] = tty_path
    payload["updated_at"] = int(time.time())
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_cache(tty_path: str | None) -> dict[str, Any] | None:
    path = cache_path_for_tty(tty_path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def record_run(started_at: int | None, cwd: str | None, tty_path: str | None) -> int:
    session_file = find_session_file(cwd, started_at)
    if session_file is None:
        return 1
    snapshot = parse_snapshot(session_file)
    if snapshot is None:
        return 1
    write_cache(tty_path, snapshot)
    return 0


def render_prompt(tty_path: str | None, cwd: str | None, max_age_seconds: int) -> str:
    payload = read_cache(tty_path)
    if not payload:
        return ""
    updated_at = safe_int(payload.get("updated_at"))
    if updated_at is None or int(time.time()) - updated_at > max_age_seconds:
        return ""
    if not cwd_matches(cwd, payload.get("cwd")):
        return ""
    summary = payload.get("summary_text")
    return summary if isinstance(summary, str) else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Track and render compact Codex session status for ExoCortex.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record", help="Store a compact summary for the most recent Codex run.")
    record_parser.add_argument("--started-at", type=int, required=True, help="Epoch seconds when Codex was launched.")
    record_parser.add_argument("--cwd", required=False, help="Working directory used for the Codex run.")
    record_parser.add_argument("--tty", required=False, help="TTY path for the current terminal.")

    prompt_parser = subparsers.add_parser("prompt", help="Print prompt text for the current terminal.")
    prompt_parser.add_argument("--cwd", required=False, help="Current working directory.")
    prompt_parser.add_argument("--tty", required=False, help="TTY path for the current terminal.")
    prompt_parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
        help="Hide stale status entries older than this many seconds.",
    )

    args = parser.parse_args()
    if args.command == "record":
        return record_run(args.started_at, args.cwd, args.tty)
    if args.command == "prompt":
        text = render_prompt(args.tty, args.cwd, args.max_age_seconds)
        if text:
            print(text)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
