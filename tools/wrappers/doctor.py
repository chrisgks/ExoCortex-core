#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.wrappers import exocortex_wrapper as wrapper


COMMANDS = ("codex", "claude", "gemini")


@dataclass
class CommandReport:
    name: str
    wrapper_path: str
    current_resolution: str | None
    current_resolves_wrapper: bool
    shell_resolution: str | None
    shell_resolves_wrapper: bool | None
    real_binary: str | None
    help_ok: bool
    help_excerpt: str
    issues: list[str]


@dataclass
class PreloadDoctorReport:
    active: bool
    total_chars: int
    hit_total_cap: bool
    files: list[str]
    missing_files: list[str]


def path_contains_entry(path_value: str, entry: Path) -> bool:
    target = entry.resolve()
    for raw in path_value.split(os.pathsep):
        if not raw:
            continue
        try:
            if Path(raw).expanduser().resolve() == target:
                return True
        except OSError:
            continue
    return False


def current_resolution_is_wrapper(resolved_path: str | None, wrapper_path: Path) -> bool:
    if not resolved_path:
        return False
    try:
        return Path(resolved_path).resolve() == wrapper_path.resolve()
    except OSError:
        return False


def shell_resolution_is_wrapper(shell_output: str, wrapper_path: Path, current_ok: bool) -> bool:
    normalized = shell_output.strip().lower()
    wrapper_texts = {str(wrapper_path).lower()}
    try:
        wrapper_texts.add(str(wrapper_path.resolve()).lower())
    except OSError:
        pass
    if any(wrapper_text in normalized for wrapper_text in wrapper_texts):
        return True
    if "shell function" in normalized and current_ok:
        return True
    return False


def one_line_excerpt(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def shell_type(shell: str | None, command: str, wrapper_path: Path, current_ok: bool) -> tuple[str | None, bool | None]:
    if not shell:
        return None, None
    shell_path = Path(shell)
    if not shell_path.exists():
        return None, None
    result = subprocess.run(
        [str(shell_path), "-lic", f"type {command}"],
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = result.stdout.strip()
    if stdout:
        return stdout, shell_resolution_is_wrapper(stdout, wrapper_path, current_ok)
    return None, None


def wrapper_help(wrapper_path: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [str(wrapper_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    excerpt = one_line_excerpt(result.stdout) or one_line_excerpt(result.stderr)
    return result.returncode == 0, excerpt


def command_report(
    root: Path,
    wrapper_bin: Path,
    command: str,
    shell: str | None,
    check_shell: bool,
) -> CommandReport:
    wrapper_path = wrapper_bin / command
    current_resolution = shutil.which(command)
    current_ok = current_resolution_is_wrapper(current_resolution, wrapper_path)
    shell_output, shell_ok = shell_type(shell, command, wrapper_path, current_ok)
    help_ok, help_excerpt = wrapper_help(wrapper_path)
    issues: list[str] = []

    real_binary = None
    try:
        real_binary = wrapper.find_real_binary(command)
    except RuntimeError as exc:
        issues.append(str(exc))

    if not wrapper_path.exists():
        issues.append(f"Missing wrapper entrypoint: {wrapper_path}")
    elif not os.access(wrapper_path, os.X_OK):
        issues.append(f"Wrapper entrypoint is not executable: {wrapper_path}")

    if not current_ok:
        issues.append(f"Current environment resolves `{command}` to `{current_resolution}` instead of the wrapper.")
    if check_shell and shell_output is None:
        issues.append(f"Could not confirm `{command}` resolution from the login shell.")
    elif check_shell and shell_ok is False:
        issues.append(f"Login shell resolution for `{command}` does not look wrapped: {one_line_excerpt(shell_output)}")
    if not help_ok:
        issues.append(f"`{wrapper_path} --help` failed.")

    return CommandReport(
        name=command,
        wrapper_path=str(wrapper_path),
        current_resolution=current_resolution,
        current_resolves_wrapper=current_ok,
        shell_resolution=one_line_excerpt(shell_output or ""),
        shell_resolves_wrapper=shell_ok,
        real_binary=real_binary,
        help_ok=help_ok,
        help_excerpt=help_excerpt,
        issues=issues,
    )


def preload_report_for_cwd(root: Path, cwd: Path) -> PreloadDoctorReport:
    agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
    mode = wrapper.default_mode(agent)
    context = wrapper.collect_context(root, cwd, agent, mode)
    preload = wrapper.load_authoritative_preload(context)
    return PreloadDoctorReport(
        active=preload.active,
        total_chars=preload.total_chars,
        hit_total_cap=preload.hit_total_cap,
        files=[item.path for item in preload.files],
        missing_files=preload.missing_files,
    )


def render_text(
    root: Path,
    wrapper_bin: Path,
    path_ok: bool,
    reports: list[CommandReport],
    preload: PreloadDoctorReport,
) -> str:
    lines = [
        "ExoCortex Wrapper Doctor",
        "",
        f"root: {root}",
        f"wrapper_bin: {wrapper_bin}",
        f"shell: {os.environ.get('SHELL', '<unknown>')}",
        f"wrapper_bin_on_path: {'ok' if path_ok else 'fail'}",
        f"authoritative_preload: {'ok' if preload.active else 'fail'}",
        f"preload_total_chars: {preload.total_chars}",
        f"preload_hit_total_cap: {'yes' if preload.hit_total_cap else 'no'}",
        "",
    ]
    if preload.files:
        lines.append("preloaded_files:")
        for path in preload.files:
            lines.append(f"  - {path}")
        lines.append("")
    if preload.missing_files:
        lines.append("missing_preload_files:")
        for path in preload.missing_files:
            lines.append(f"  - {path}")
        lines.append("")
    for report in reports:
        status = "ok" if not report.issues else "fail"
        lines.extend(
            [
                f"[{status}] {report.name}",
                f"  current_resolution: {report.current_resolution or '<missing>'}",
                f"  login_shell_type: {report.shell_resolution or '<unavailable>'}",
                f"  real_binary: {report.real_binary or '<missing>'}",
                f"  wrapper_help: {'ok' if report.help_ok else 'fail'}"
                + (f" ({report.help_excerpt})" if report.help_excerpt else ""),
            ]
        )
        for issue in report.issues:
            lines.append(f"  issue: {issue}")
        lines.append("")
    if not path_ok:
        lines.append("Hint: source your shell config or re-run tools/wrappers/install.sh so the wrapper bin is first on PATH.")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ExoCortex wrapper installation and runtime resolution.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument(
        "--skip-shell-check",
        action="store_true",
        help="Skip the login-shell resolution check and only verify the current environment plus wrapper entrypoints.",
    )
    args = parser.parse_args()

    root = wrapper.exocortex_root()
    wrapper_bin = root / "tools" / "wrappers" / "bin"
    path_ok = path_contains_entry(os.environ.get("PATH", ""), wrapper_bin)
    check_shell = not args.skip_shell_check
    shell = os.environ.get("SHELL") if check_shell else None
    preload = preload_report_for_cwd(root, Path.cwd())
    reports = [command_report(root, wrapper_bin, command, shell, check_shell) for command in COMMANDS]
    ok = path_ok and preload.active and all(not report.issues for report in reports)

    if args.json:
        payload = {
            "ok": ok,
            "root": str(root),
            "wrapper_bin": str(wrapper_bin),
            "shell": shell,
            "wrapper_bin_on_path": path_ok,
            "authoritative_preload": asdict(preload),
            "commands": [asdict(report) for report in reports],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_text(root, wrapper_bin, path_ok, reports, preload))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
