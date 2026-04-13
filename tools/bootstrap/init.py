#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent


ROOT_MARKERS = ("AGENT.md", "README.md", "agents", "domains", "system", "tools")


def titleize(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").strip().title()


def render_template(path: Path, values: dict[str, str]) -> str:
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def find_repo_root(start: Path) -> Path:
    start = start.resolve()
    for candidate in (start, *start.parents):
        if all((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    raise RuntimeError(f"Could not find an ExoCortex root above {start}")


@dataclass
class WriteSummary:
    created: list[Path]
    preserved: list[Path]


def ensure_text(path: Path, content: str, force: bool, summary: WriteSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        summary.preserved.append(path)
        return
    path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")
    summary.created.append(path)


def ensure_repo_runtime(root: Path, force: bool) -> WriteSummary:
    summary = WriteSummary(created=[], preserved=[])
    runtime_files = {
        "journal/inbox/README.md": "# Review Inbox\n\nThis folder holds human-review queues and machine-readable ledgers derived from session processing.\n",
        "journal/inbox/pending-intents.md": "# Pending Intents\n\nNo pending inferred intents yet.\n",
        "journal/inbox/reviewed-intents.md": "# Reviewed Intents\n\nNo reviewed intent decisions yet.\n",
        "journal/inbox/intent-review-state.json": '{\n  "pending": [],\n  "reviewed": []\n}\n',
        "raw/assets/README.md": "# Raw Assets\n\nPut local images and source attachments here when they belong to the raw-source layer.\n",
        "raw/sources/README.md": "# Curated Sources\n\nPut curated raw source files here after triage.\n",
        "raw/inbox/concept_seeds/README.md": "# Concept Seeds\n\nDrop interesting thoughts or fragments here before they are routed into writing, memory, or a wiki.\n",
        "raw/inbox/design_notes/README.md": "# Design Notes\n\nUse this bucket for internal architecture notes that have not yet been turned into curated source or wiki pages.\n",
        "raw/inbox/webpage_captures/README.md": "# Webpage Captures\n\nUse this bucket for captured web pages that still need categorization or source-note creation.\n",
        "wiki/index.md": """---
title: Index
type: meta
status: active
created: 2026-04-13
updated: 2026-04-13
summary: Main navigation page for the root ExoCortex wiki.
source_count: 0
tags: index, root-wiki
---

# Index

This is the first file the LLM should read when navigating the root wiki.

## Overviews

- [[Overview]] - Start-here summary for the root managed wiki.

## Meta

- [[Scope]] - Scope contract for the root wiki.
- [[Operating Contract]] - Authoritative rules for managed wiki work and raw-source handling.
- [[Maintenance]] - Operational guidance for keeping the wiki healthy.
- [[Backlog]] - Open maintenance and research items for this wiki.
- [[Log]] - Append-only chronological record of wiki operations.
""",
        "wiki/01_overviews/Overview.md": """---
title: Overview
type: overview
status: seed
created: 2026-04-13
updated: 2026-04-13
summary: Start-here summary for the root ExoCortex wiki.
source_count: 0
tags: overview
---

# Overview

This root wiki starts empty on purpose.

Use it for ExoCortex-wide architecture, policy, and cross-context knowledge after repeated evidence justifies a root-scope page.

## Sources

- None yet.

## Open Questions

- Which knowledge should stay local to a project or domain wiki instead of being promoted to root?
""",
        "wiki/00_meta/Scope.md": """---
title: Scope
type: meta
status: active
created: 2026-04-13
updated: 2026-04-13
summary: Scope contract for the root ExoCortex wiki.
source_count: 0
tags: meta, scope, root-wiki
---

# Scope

- `context_path`: `.`
- `scope`: `root`
- `owner_agent`: `knowledge-steward`
- `parent_wiki`: none
- `child_wikis`: none yet
- `promotion_rule`: keep knowledge local by default and promote into the root wiki only when it becomes reusable across contexts inside ExoCortex
""",
        "wiki/00_meta/Maintenance.md": """---
title: Maintenance
type: meta
status: active
created: 2026-04-13
updated: 2026-04-13
summary: Operational guidance for maintaining the root wiki.
source_count: 0
tags: meta, maintenance
---

# Maintenance

## Rules

- Read `index.md` before substantial wiki work.
- Keep root-wiki content sparse and reusable.
- Update `index.md` and `log.md` after substantial changes.
- Prefer editing an existing page over creating a redundant new one.
""",
        "wiki/00_meta/Backlog.md": """---
title: Backlog
type: meta
status: active
created: 2026-04-13
updated: 2026-04-13
summary: Open maintenance and research items for the root wiki.
source_count: 0
tags: meta, backlog
---

# Backlog

- Decide when a project deserves its own managed wiki.
- Add the first real root-scope overview or analysis page when repeated evidence justifies it.
""",
        "wiki/log.md": """---
title: Log
type: meta
status: active
created: 2026-04-13
updated: 2026-04-13
summary: Append-only chronological record of wiki operations.
source_count: 0
tags: log
---

# Log

Use one heading per operation so the file stays chronologically legible to both humans and LLMs.

## [2026-04-13] setup | Root wiki scaffold

        - Details: Created the clean public root wiki scaffold.
""",
    }
    for relative_path, content in runtime_files.items():
        ensure_text(root / relative_path, content, force, summary)
    return summary


def scaffold_from_templates(
    *,
    root: Path,
    force: bool,
    templates: list[tuple[str, str]],
    values: dict[str, str],
) -> WriteSummary:
    summary = WriteSummary(created=[], preserved=[])
    template_root = root / "templates"
    for template_relative, destination_relative in templates:
        rendered = render_template(template_root / template_relative, values)
        ensure_text(root / destination_relative, rendered, force, summary)
    return summary


def init_domain(root: Path, name: str, force: bool) -> WriteSummary:
    title = titleize(name)
    return scaffold_from_templates(
        root=root,
        force=force,
        templates=[
            ("domain-context/README.template.md", f"domains/{name}/README.md"),
            ("domain-context/AGENT.template.md", f"domains/{name}/AGENT.md"),
            ("domain-context/projects.README.template.md", f"domains/{name}/projects/README.md"),
        ],
        values={"DOMAIN_NAME": name, "DOMAIN_TITLE": title},
    )


def init_project(root: Path, domain: str, name: str, force: bool) -> WriteSummary:
    domain_summary = init_domain(root, domain, force=False)
    title = titleize(name)
    project_summary = scaffold_from_templates(
        root=root,
        force=force,
        templates=[
            ("project-context/README.template.md", f"domains/{domain}/projects/{name}/README.md"),
            ("project-context/AGENT.template.md", f"domains/{domain}/projects/{name}/AGENT.md"),
            ("project-context/MEMORY.template.md", f"domains/{domain}/projects/{name}/MEMORY.md"),
            ("project-context/STATE.template.md", f"domains/{domain}/projects/{name}/STATE.md"),
            ("project-context/WORKFLOWS.template.md", f"domains/{domain}/projects/{name}/WORKFLOWS.md"),
        ],
        values={
            "DOMAIN_NAME": domain,
            "DOMAIN_TITLE": titleize(domain),
            "PROJECT_NAME": name,
            "PROJECT_TITLE": title,
        },
    )
    return WriteSummary(
        created=domain_summary.created + project_summary.created,
        preserved=domain_summary.preserved + project_summary.preserved,
    )


def install_wrappers(root: Path, shell_file: str | None) -> None:
    command = [str(root / "tools" / "wrappers" / "install.sh")]
    if shell_file:
        command.append(shell_file)
    subprocess.run(command, check=True)


def install_cron(root: Path) -> None:
    command = [str(root / "tools" / "automations" / "install_cron.sh")]
    subprocess.run(command, check=True)


def print_summary(label: str, summary: WriteSummary) -> None:
    print(label)
    print(f"  created: {len(summary.created)}")
    print(f"  preserved: {len(summary.preserved)}")
    if summary.created:
        print("  new files:")
        for path in summary.created:
            print(f"    - {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize a clean ExoCortex clone or scaffold new contexts.")
    parser.add_argument("--path", default=".", help="Path inside the target ExoCortex repo. Defaults to the current directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite bootstrap-managed files when they already exist.")
    parser.add_argument("--install-wrappers", action="store_true", help="Run tools/wrappers/install.sh after repo initialization.")
    parser.add_argument("--install-cron", action="store_true", help="Run tools/automations/install_cron.sh after repo initialization.")
    parser.add_argument("--shell-file", help="Shell file to pass to tools/wrappers/install.sh.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("repo", help="Initialize or restore the clean runtime scaffold in the current ExoCortex clone.")

    domain_parser = subparsers.add_parser("domain", help="Scaffold a new domain from templates.")
    domain_parser.add_argument("name")

    project_parser = subparsers.add_parser("project", help="Scaffold a new project inside a domain from templates.")
    project_parser.add_argument("domain")
    project_parser.add_argument("name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = find_repo_root(Path(args.path))
    command = args.command or "repo"

    if command == "repo":
        summary = ensure_repo_runtime(root, args.force)
        print_summary("ExoCortex repo bootstrap complete.", summary)
        if args.install_wrappers or args.shell_file:
            install_wrappers(root, args.shell_file)
            print("Wrapper install complete.")
        if args.install_cron:
            install_cron(root)
            print("Cron install complete.")
        print("Next steps:")
        print("  1. Run exocortex-doctor after opening a fresh shell.")
        print("  2. Install cron automation with --install-cron or tools/automations/install_cron.sh if you have not already.")
        print("  3. Start a wrapped harness from the repo root or a narrower folder.")
        return 0

    if command == "domain":
        summary = init_domain(root, args.name, args.force)
        print_summary(f"Domain scaffolded: {args.name}", summary)
        return 0

    if command == "project":
        summary = init_project(root, args.domain, args.name, args.force)
        print_summary(f"Project scaffolded: {args.domain}/{args.name}", summary)
        return 0

    raise RuntimeError(f"Unsupported command: {command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
