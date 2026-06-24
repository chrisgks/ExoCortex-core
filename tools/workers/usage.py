#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.instance import resolve_instance_root


RATES_PATH = Path("system/USAGE RATES.json")
LEDGER_PATH = Path("journal/usage/usage-ledger.jsonl")
DAILY_DIR = Path("journal/usage/daily")
DEFAULT_PRICING_VERSION = "2026-04-29-openai-pricing"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
CLAUDE_PROJECTS_DIR = Path(os.environ.get("EXOCORTEX_CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))
GEMINI_TMP_DIR = Path(os.environ.get("EXOCORTEX_GEMINI_TMP_DIR", str(Path.home() / ".gemini" / "tmp")))
SESSION_MATCH_SLOP_SECONDS = 10


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    tool_tokens: int = 0
    total_tokens: int = 0
    model_context_window: int | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
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


def format_usd(value: float | None) -> str:
    if value is None:
        return "$?"
    if value < 0.01:
        return f"${value:.4f}"
    if value < 10:
        return f"${value:.2f}"
    return f"${value:,.2f}"


def load_rates(root: Path) -> dict[str, Any]:
    path = root / RATES_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": DEFAULT_PRICING_VERSION, "models": {}}


def normalize_model(model: str | None) -> str | None:
    if not model:
        return None
    normalized = model.strip().lower()
    if normalized.startswith("models/"):
        normalized = normalized.removeprefix("models/")
    return normalized


def model_rates(pricing: dict[str, Any], model: str | None) -> dict[str, Any] | None:
    normalized = normalize_model(model)
    if not normalized:
        return None
    models = pricing.get("models")
    if not isinstance(models, dict):
        return None
    if normalized in models:
        return models[normalized]
    aliases = pricing.get("aliases")
    if isinstance(aliases, dict):
        target = aliases.get(normalized)
        if isinstance(target, str) and target in models:
            return models[target]
    return None


def cost_usd(usage: TokenUsage, rates: dict[str, Any] | None) -> float | None:
    if not rates:
        return None
    input_rate = safe_float(rates.get("input_usd_per_1m"))
    cached_rate = safe_float(rates.get("cached_input_usd_per_1m"))
    cache_creation_rate = safe_float(rates.get("cache_creation_input_usd_per_1m"))
    cache_read_rate = safe_float(rates.get("cache_read_input_usd_per_1m"))
    output_rate = safe_float(rates.get("output_usd_per_1m"))
    if input_rate is None or output_rate is None:
        return None
    output_tokens = usage.output_tokens
    if cache_creation_rate is not None or cache_read_rate is not None:
        creation_rate = cache_creation_rate if cache_creation_rate is not None else input_rate
        read_rate = cache_read_rate if cache_read_rate is not None else cached_rate
        if read_rate is None:
            read_rate = input_rate
        if usage.cache_creation_input_tokens or usage.cache_read_input_tokens:
            read_tokens = usage.cache_read_input_tokens
        else:
            read_tokens = usage.cached_input_tokens
        cost = (
            usage.input_tokens * input_rate
            + usage.cache_creation_input_tokens * creation_rate
            + read_tokens * read_rate
            + output_tokens * output_rate
        )
        return round(cost / 1_000_000, 6)
    if cached_rate is None:
        cached_rate = input_rate
    cached = min(usage.cached_input_tokens, usage.input_tokens)
    uncached = max(usage.input_tokens - cached, 0)
    return round((uncached * input_rate + cached * cached_rate + output_tokens * output_rate) / 1_000_000, 6)


def total_input_tokens(usage: TokenUsage) -> int:
    return usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens


def total_cached_tokens(usage: TokenUsage) -> int:
    if usage.cache_creation_input_tokens or usage.cache_read_input_tokens:
        return usage.cache_creation_input_tokens + usage.cache_read_input_tokens
    return usage.cached_input_tokens


def extract_usage_from_token_info(info: dict[str, Any]) -> TokenUsage:
    total_usage = info.get("total_token_usage")
    if not isinstance(total_usage, dict):
        total_usage = {}
    return TokenUsage(
        input_tokens=safe_int(total_usage.get("input_tokens")),
        cached_input_tokens=safe_int(total_usage.get("cached_input_tokens")),
        output_tokens=safe_int(total_usage.get("output_tokens")),
        reasoning_output_tokens=safe_int(total_usage.get("reasoning_output_tokens")),
        total_tokens=safe_int(total_usage.get("total_tokens")),
        model_context_window=safe_int(info.get("model_context_window")) or None,
    )


def token_info_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    return info if isinstance(info, dict) else None


def model_from_event(event: dict[str, Any]) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    for key in ("model", "model_slug", "model_name"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    if event.get("type") == "turn_context":
        value = payload.get("model")
        if isinstance(value, str):
            return value
    return None


def provider_from_event(event: dict[str, Any]) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("model_provider")
    return value if isinstance(value, str) else None


def codex_usage_from_event(
    root: Path,
    event: dict[str, Any],
    model: str | None,
) -> tuple[TokenUsage | None, float | None, dict[str, Any] | None]:
    info = token_info_from_event(event)
    if info is None:
        return None, None, None
    usage = extract_usage_from_token_info(info)
    pricing = load_rates(root)
    rates = model_rates(pricing, model)
    return usage, cost_usd(usage, rates), rates


def read_codex_session_snapshot(path: Path) -> dict[str, Any] | None:
    session_id: str | None = None
    cwd: str | None = None
    provider: str | None = None
    model: str | None = None
    cli_version: str | None = None
    usage: TokenUsage | None = None

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
                payload = event.get("payload")
                if event.get("type") == "session_meta" and isinstance(payload, dict):
                    session_id = payload.get("id") if isinstance(payload.get("id"), str) else session_id
                    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else cwd
                    provider = payload.get("model_provider") if isinstance(payload.get("model_provider"), str) else provider
                    cli_version = payload.get("cli_version") if isinstance(payload.get("cli_version"), str) else cli_version
                event_model = model_from_event(event)
                if event_model:
                    model = event_model
                event_provider = provider_from_event(event)
                if event_provider:
                    provider = event_provider
                info = token_info_from_event(event)
                if info is not None:
                    usage = extract_usage_from_token_info(info)
    except OSError:
        return None

    if usage is None:
        return None
    return {
        "tool": "codex",
        "source_kind": "codex_session_jsonl",
        "codex_session_id": session_id,
        "codex_session_file": str(path),
        "cwd": cwd,
        "provider": provider or "openai",
        "model": model,
        "cli_version": cli_version,
        "usage": usage,
    }


def normalize_path(path: str | Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser())


def paths_match(expected: str | Path | None, recorded: str | Path | None) -> bool:
    expected_path = normalize_path(expected)
    recorded_path = normalize_path(recorded)
    if not expected_path or not recorded_path:
        return True
    return expected_path == recorded_path or expected_path.startswith(recorded_path + os.sep) or recorded_path.startswith(expected_path + os.sep)


def recent_files(base: Path, pattern: str, started_at_epoch: int | None) -> list[Path]:
    if not base.exists():
        return []
    threshold = None if started_at_epoch is None else started_at_epoch - SESSION_MATCH_SLOP_SECONDS
    candidates: list[Path] = []
    for path in base.rglob(pattern):
        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            continue
        if threshold is not None and mtime < threshold:
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)


def read_codex_session_cwd(path: Path) -> str | None:
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
                if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
                    return payload["cwd"]
    except OSError:
        return None
    return None


def find_codex_session_file(cwd: str | Path | None, started_at_epoch: int | None) -> Path | None:
    for path in recent_files(CODEX_SESSIONS_DIR, "rollout-*.jsonl", started_at_epoch):
        if paths_match(cwd, read_codex_session_cwd(path)):
            return path
    return None


def claude_usage_from_payload(payload: dict[str, Any]) -> TokenUsage:
    return TokenUsage(
        input_tokens=safe_int(payload.get("input_tokens")),
        cache_creation_input_tokens=safe_int(payload.get("cache_creation_input_tokens")),
        cache_read_input_tokens=safe_int(payload.get("cache_read_input_tokens")),
        cached_input_tokens=safe_int(payload.get("cache_creation_input_tokens")) + safe_int(payload.get("cache_read_input_tokens")),
        output_tokens=safe_int(payload.get("output_tokens")),
        total_tokens=(
            safe_int(payload.get("input_tokens"))
            + safe_int(payload.get("cache_creation_input_tokens"))
            + safe_int(payload.get("cache_read_input_tokens"))
            + safe_int(payload.get("output_tokens"))
        ),
    )


def read_claude_session_snapshot(path: Path) -> dict[str, Any] | None:
    session_id: str | None = None
    cwd: str | None = None
    model: str | None = None
    cli_version: str | None = None
    usage = TokenUsage()
    seen_messages: set[str] = set()
    saw_usage = False

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
                if isinstance(event.get("sessionId"), str):
                    session_id = event["sessionId"]
                if isinstance(event.get("cwd"), str):
                    cwd = event["cwd"]
                if isinstance(event.get("version"), str):
                    cli_version = event["version"]
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                if isinstance(message.get("model"), str):
                    model = message["model"]
                payload = message.get("usage")
                if not isinstance(payload, dict):
                    continue
                message_id = message.get("id")
                if isinstance(message_id, str) and message_id in seen_messages:
                    continue
                if isinstance(message_id, str):
                    seen_messages.add(message_id)
                turn_usage = claude_usage_from_payload(payload)
                usage.input_tokens += turn_usage.input_tokens
                usage.cache_creation_input_tokens += turn_usage.cache_creation_input_tokens
                usage.cache_read_input_tokens += turn_usage.cache_read_input_tokens
                usage.cached_input_tokens += turn_usage.cached_input_tokens
                usage.output_tokens += turn_usage.output_tokens
                usage.total_tokens += turn_usage.total_tokens
                saw_usage = True
    except OSError:
        return None

    if not saw_usage:
        return None
    return {
        "tool": "claude",
        "source_kind": "claude_session_jsonl",
        "claude_session_id": session_id,
        "claude_session_file": str(path),
        "cwd": cwd,
        "provider": "anthropic",
        "model": model,
        "cli_version": cli_version,
        "usage": usage,
    }


def read_claude_session_cwd(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event.get("cwd"), str):
                    return event["cwd"]
    except OSError:
        return None
    return None


def find_claude_session_file(cwd: str | Path | None, started_at_epoch: int | None) -> Path | None:
    for path in recent_files(CLAUDE_PROJECTS_DIR, "*.jsonl", started_at_epoch):
        if paths_match(cwd, read_claude_session_cwd(path)):
            return path
    return None


def read_gemini_session_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    session_id = payload.get("sessionId") or payload.get("id") or path.stem
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    cli_version = payload.get("version") if isinstance(payload.get("version"), str) else None
    messages = payload.get("messages")
    if not isinstance(messages, list):
        messages = payload.get("history")
    if not isinstance(messages, list):
        return None

    usage = TokenUsage()
    model: str | None = payload.get("model") if isinstance(payload.get("model"), str) else None
    saw_usage = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if isinstance(message.get("model"), str):
            model = message["model"]
        tokens = message.get("tokens")
        if not isinstance(tokens, dict):
            continue
        input_tokens = safe_int(tokens.get("input"))
        cached_tokens = safe_int(tokens.get("cached"))
        output_tokens = safe_int(tokens.get("output"))
        thinking_tokens = safe_int(tokens.get("thoughts"))
        tool_tokens = safe_int(tokens.get("tool"))
        total_tokens_value = safe_int(tokens.get("total"))
        usage.input_tokens += input_tokens
        usage.cached_input_tokens += cached_tokens
        usage.output_tokens += output_tokens + thinking_tokens
        usage.reasoning_output_tokens += thinking_tokens
        usage.tool_tokens += tool_tokens
        usage.total_tokens += total_tokens_value or (input_tokens + output_tokens + thinking_tokens + tool_tokens)
        saw_usage = True

    if not saw_usage:
        return None
    return {
        "tool": "gemini",
        "source_kind": "gemini_session_json",
        "gemini_session_id": session_id if isinstance(session_id, str) else str(session_id),
        "gemini_session_file": str(path),
        "cwd": cwd,
        "provider": "google",
        "model": model,
        "cli_version": cli_version,
        "usage": usage,
    }


def find_gemini_session_file(cwd: str | Path | None, started_at_epoch: int | None) -> Path | None:
    candidates = recent_files(GEMINI_TMP_DIR, "session-*.json", started_at_epoch)
    expected_cwd = normalize_path(cwd)
    if expected_cwd:
        expected_name = Path(expected_cwd).name.lower()
        for path in candidates:
            if expected_name and expected_name in str(path).lower():
                return path
    return candidates[0] if candidates else None


def find_harness_session_file(tool: str, cwd: str | Path | None, started_at_epoch: int | None) -> Path | None:
    if tool == "codex":
        return find_codex_session_file(cwd, started_at_epoch)
    if tool == "claude":
        return find_claude_session_file(cwd, started_at_epoch)
    if tool == "gemini":
        return find_gemini_session_file(cwd, started_at_epoch)
    return None


def read_harness_session_snapshot(tool: str, path: Path) -> dict[str, Any] | None:
    if tool == "codex":
        return read_codex_session_snapshot(path)
    if tool == "claude":
        return read_claude_session_snapshot(path)
    if tool == "gemini":
        return read_gemini_session_snapshot(path)
    return None


def build_record(
    root: Path,
    *,
    manifest: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    pricing = load_rates(root)
    usage = snapshot["usage"]
    rates = model_rates(pricing, snapshot.get("model"))
    cost = cost_usd(usage, rates)
    model = snapshot.get("model")
    cost_basis = "actual_tokens_priced" if cost is not None else "actual_tokens_unpriced"
    cached_tokens = total_cached_tokens(usage)
    source_kind = snapshot.get("source_kind") or "session_usage"
    source: dict[str, Any] = {
        "kind": source_kind,
        "cli_version": snapshot.get("cli_version"),
    }
    for key in (
        "codex_session_id",
        "codex_session_file",
        "claude_session_id",
        "claude_session_file",
        "gemini_session_id",
        "gemini_session_file",
    ):
        if snapshot.get(key) is not None:
            source[key] = snapshot.get(key)
    if usage.cache_creation_input_tokens or usage.cache_read_input_tokens:
        billable_uncached_input_tokens = usage.input_tokens
    else:
        billable_uncached_input_tokens = max(usage.input_tokens - usage.cached_input_tokens, 0)
    return {
        "schema_version": 1,
        "recorded_at": now_iso(),
        "session_id": manifest.get("session_id"),
        "tool": manifest.get("tool") or snapshot.get("tool"),
        "provider": snapshot.get("provider"),
        "model": model,
        "pricing_version": pricing.get("version", DEFAULT_PRICING_VERSION),
        "pricing_source": pricing.get("source"),
        "pricing_effective": pricing.get("effective_date"),
        "cost_basis": cost_basis,
        "cost_usd": cost,
        "input_tokens": usage.input_tokens,
        "total_input_tokens": total_input_tokens(usage),
        "cached_input_tokens": cached_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "billable_uncached_input_tokens": billable_uncached_input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_output_tokens": usage.reasoning_output_tokens,
        "tool_tokens": usage.tool_tokens,
        "total_tokens": usage.total_tokens,
        "model_context_window": usage.model_context_window,
        "cwd": manifest.get("cwd") or snapshot.get("cwd"),
        "started_at": manifest.get("started_at"),
        "ended_at": manifest.get("ended_at"),
        "source": source,
    }


def usage_summary_text(record: dict[str, Any]) -> str:
    model = record.get("model") or "unknown-model"
    cost = format_usd(record.get("cost_usd"))
    input_tokens = record.get("total_input_tokens") or record.get("input_tokens")
    return (
        f"session {cost} | in {format_tokens(input_tokens)} "
        f"(cached {format_tokens(record.get('cached_input_tokens'))}) | "
        f"out {format_tokens(record.get('output_tokens'))} | {model} | {record.get('cost_basis')}"
    )


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def record_exists(root: Path, session_id: str | None) -> bool:
    if not session_id:
        return False
    ledger = root / LEDGER_PATH
    if not ledger.exists():
        return False
    try:
        with ledger.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if record.get("session_id") == session_id:
                    return True
    except OSError:
        return False
    return False


def update_daily_rollup(root: Path, record: dict[str, Any]) -> None:
    date = (record.get("ended_at") or record.get("started_at") or record.get("recorded_at") or now_iso())[:10]
    path = root / DAILY_DIR / f"{date}.json"
    existing = {
        "date": date,
        "session_count": 0,
        "cost_usd": 0.0,
        "input_tokens": 0,
        "total_input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "tool_tokens": 0,
        "total_tokens": 0,
        "by_model": {},
    }
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing.update(loaded)
        except json.JSONDecodeError:
            pass
    existing["session_count"] = safe_int(existing.get("session_count")) + 1
    existing["cost_usd"] = round(float(existing.get("cost_usd") or 0.0) + float(record.get("cost_usd") or 0.0), 6)
    for key in (
        "input_tokens",
        "total_input_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "tool_tokens",
        "total_tokens",
    ):
        existing[key] = safe_int(existing.get(key)) + safe_int(record.get(key))
    model = record.get("model") or "unknown"
    by_model = existing.setdefault("by_model", {})
    bucket = by_model.setdefault(model, {"session_count": 0, "cost_usd": 0.0, "total_tokens": 0})
    bucket["session_count"] = safe_int(bucket.get("session_count")) + 1
    bucket["cost_usd"] = round(float(bucket.get("cost_usd") or 0.0) + float(record.get("cost_usd") or 0.0), 6)
    bucket["total_tokens"] = safe_int(bucket.get("total_tokens")) + safe_int(record.get("total_tokens"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_record(root: Path, record: dict[str, Any]) -> bool:
    if record_exists(root, record.get("session_id")):
        return False
    append_jsonl(root / LEDGER_PATH, record)
    update_daily_rollup(root, record)
    return True


def record_codex_session(root: Path, manifest: dict[str, Any], session_file: Path) -> dict[str, Any] | None:
    snapshot = read_codex_session_snapshot(session_file)
    if snapshot is None:
        return None
    record = build_record(root, manifest=manifest, snapshot=snapshot)
    append_record(root, record)
    return record


def record_harness_session(
    root: Path,
    manifest: dict[str, Any],
    tool: str,
    session_file: Path,
) -> dict[str, Any] | None:
    snapshot = read_harness_session_snapshot(tool, session_file)
    if snapshot is None:
        return None
    record = build_record(root, manifest=manifest, snapshot=snapshot)
    append_record(root, record)
    return record


def load_records(root: Path) -> list[dict[str, Any]]:
    ledger = root / LEDGER_PATH
    if not ledger.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with ledger.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records


def parse_period(period: str) -> datetime:
    now = datetime.now(timezone.utc)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return datetime.fromisoformat(period).replace(tzinfo=timezone.utc)


def filter_since(records: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    selected = []
    for record in records:
        timestamp = record.get("ended_at") or record.get("started_at") or record.get("recorded_at")
        if not isinstance(timestamp, str):
            continue
        try:
            dt = datetime.fromisoformat(timestamp)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= since:
            selected.append(record)
    return selected


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "session_count": len(records),
        "cost_usd": round(sum(float(record.get("cost_usd") or 0.0) for record in records), 6),
        "input_tokens": sum(safe_int(record.get("input_tokens")) for record in records),
        "total_input_tokens": sum(safe_int(record.get("total_input_tokens")) for record in records),
        "cached_input_tokens": sum(safe_int(record.get("cached_input_tokens")) for record in records),
        "cache_creation_input_tokens": sum(safe_int(record.get("cache_creation_input_tokens")) for record in records),
        "cache_read_input_tokens": sum(safe_int(record.get("cache_read_input_tokens")) for record in records),
        "output_tokens": sum(safe_int(record.get("output_tokens")) for record in records),
        "reasoning_output_tokens": sum(safe_int(record.get("reasoning_output_tokens")) for record in records),
        "tool_tokens": sum(safe_int(record.get("tool_tokens")) for record in records),
        "total_tokens": sum(safe_int(record.get("total_tokens")) for record in records),
        "by_model": {},
    }
    by_model = summary["by_model"]
    for record in records:
        model = record.get("model") or "unknown"
        bucket = by_model.setdefault(model, {"session_count": 0, "cost_usd": 0.0, "total_tokens": 0})
        bucket["session_count"] += 1
        bucket["cost_usd"] = round(bucket["cost_usd"] + float(record.get("cost_usd") or 0.0), 6)
        bucket["total_tokens"] += safe_int(record.get("total_tokens"))
    return summary


def print_summary(summary: dict[str, Any], label: str) -> None:
    print(f"{label}: {summary['session_count']} sessions, {format_usd(summary['cost_usd'])}, {format_tokens(summary['total_tokens'])} tokens")
    for model, bucket in sorted(summary["by_model"].items()):
        print(
            f"- {model}: {bucket['session_count']} sessions, "
            f"{format_usd(bucket['cost_usd'])}, {format_tokens(bucket['total_tokens'])} tokens"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize ExoCortex token and cost usage.")
    parser.add_argument("--root", default=None, help="ExoCortex root directory.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser("summary", help="Summarize usage for today, week, month, or an ISO date.")
    summary_parser.add_argument("period", choices=["today", "week", "month"], nargs="?", default="today")
    summary_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    args = parser.parse_args(argv)
    root = resolve_instance_root(args.root)
    if args.command == "summary":
        since = parse_period(args.period)
        summary = summarize_records(filter_since(load_records(root), since))
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print_summary(summary, args.period)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
