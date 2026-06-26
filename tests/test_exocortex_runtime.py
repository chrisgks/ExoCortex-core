import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workers import context_hygiene
from tools.workers import ingest_raw
from tools.workers import intent_review
from tools.workers import process_session as worker
from tools.workers import reprocess_sessions
from tools.workers import retrieve
from tools.workers import review as review_worker
from tools.workers import wiki_map_maintain
from tools.wrappers import doctor
from tools.wrappers import exocortex_wrapper as wrapper


def write(path: Path, content: str = "# test\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def now_iso_for_test() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ExoCortexRuntimeTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        for name in ("AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "README.md", "SKILLS.md"):
            write(root / name)
        for name in (
            "README.md",
            "AGENT.md",
            "MEMORY.md",
            "STATE.md",
            "WORKFLOWS.md",
            "DECISION RULES.md",
            "INTELLIGENCE LOOP.md",
            "INTENDED BEHAVIORS.md",
            "PERSONA CALIBRATION.md",
            "HEALTH STATE.md",
            "HEALTH RULES.md",
            "OPEN LOOPS.md",
            "PRIORITIES.md",
        ):
            write(root / "system" / name)
        return temp_dir, root

    def write_contract_files(
        self,
        base: Path,
        names: tuple[str, ...] = ("README.md", "AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "SKILLS.md"),
    ) -> None:
        for name in names:
            write(base / name, f"{base.name} {name}\n")

    def append_locked_once_via_subprocess(self, path: Path, block: str, session_id: str) -> subprocess.Popen[str]:
        code = (
            "import sys;"
            "from pathlib import Path;"
            "from tools.workers import process_session as worker;"
            "worker.append_locked_once(Path(sys.argv[1]), sys.argv[2], sys.argv[3])"
        )
        return subprocess.Popen(
            [sys.executable, "-c", code, str(path), block, session_id],
            cwd=str(Path(__file__).resolve().parents[1]),
            text=True,
        )

    def test_agent_folder_selects_matching_agent_and_context(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "agents" / "builder" / "README.md")
        write(root / "agents" / "builder" / "AGENT.md")
        write(root / "agents" / "builder" / "MEMORY.md")
        write(root / "agents" / "builder" / "STATE.md")
        write(root / "agents" / "builder" / "WORKFLOWS.md")

        cwd = root / "agents" / "builder"
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))

        self.assertEqual(agent, "builder")
        labels = [entry["label"] for entry in context.visible_contexts]
        self.assertIn("agent:builder", labels)

    def test_root_context_includes_root_and_system_levels(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)
        write(root / "wiki" / "00_meta" / "Operating Contract.md", "# wiki contract\n")

        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, root), root, root)
        mode = wrapper.default_mode(agent)
        context = wrapper.collect_context(root, root, agent, mode)

        self.assertEqual(agent, "chief-of-staff")
        self.assertEqual(mode, "conversation")
        self.assertEqual(context.level, "root")
        labels = [entry["label"] for entry in context.visible_contexts]
        self.assertIn("root", labels)
        self.assertIn("system", labels)
        self.assertNotIn("local", labels)
        root_entry = next(entry for entry in context.visible_contexts if entry["label"] == "root")
        self.assertIn("wiki/00_meta/Operating Contract.md", root_entry["files"])

    def test_domain_context_matrix_uses_expected_agent_mode_and_labels(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        expected = {
            "work": ("planning", "processing"),
            "life": ("life-systems", "application"),
            "learning": ("research", "processing"),
            "writing": ("planning", "processing"),
        }
        for domain, (expected_agent, expected_mode) in expected.items():
            with self.subTest(domain=domain):
                domain_root = root / "domains" / domain
                self.write_contract_files(domain_root)

                agent = wrapper.default_agent(*wrapper.detect_domain_project(root, domain_root), domain_root, root)
                mode = wrapper.default_mode(agent)
                context = wrapper.collect_context(root, domain_root, agent, mode)

                self.assertEqual(agent, expected_agent)
                self.assertEqual(mode, expected_mode)
                self.assertEqual(context.level, "domain")
                labels = [entry["label"] for entry in context.visible_contexts]
                self.assertIn("root", labels)
                self.assertIn("system", labels)
                self.assertIn(f"domain:{domain}", labels)
                self.assertNotIn("project:demo", labels)
                self.assertNotIn("local", labels)

    def test_local_folder_contract_is_included(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "tools" / "wrappers" / "README.md")

        cwd = root / "tools" / "wrappers"
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))

        local_entries = [entry for entry in context.visible_contexts if entry["label"] == "local"]
        self.assertEqual(len(local_entries), 1)
        self.assertIn("tools/wrappers/README.md", local_entries[0]["files"])

    def test_non_domain_local_folder_defaults_to_local_level(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "tools" / "wrappers" / "README.md")

        cwd = root / "tools" / "wrappers"
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        mode = wrapper.default_mode(agent)
        context = wrapper.collect_context(root, cwd, agent, mode)

        self.assertEqual(agent, "chief-of-staff")
        self.assertEqual(mode, "conversation")
        self.assertEqual(context.level, "local")
        labels = [entry["label"] for entry in context.visible_contexts]
        self.assertIn("root", labels)
        self.assertIn("system", labels)
        self.assertIn("local", labels)

    def test_project_and_local_contexts_are_both_visible(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        project_root = root / "domains" / "work" / "projects" / "demo"
        for name in ("README.md", "AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "SKILLS.md"):
            write(root / "domains" / "work" / name)
            write(project_root / name)
        write(project_root / "package.json", "{}\n")
        write(project_root / "docs" / "README.md")

        cwd = project_root / "docs"
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))

        labels = [entry["label"] for entry in context.visible_contexts]
        self.assertEqual(agent, "builder")
        self.assertIn("domain:work", labels)
        self.assertIn("project:demo", labels)
        self.assertIn("local", labels)
        self.assertEqual(context.level, "project")

    def test_builder_context_strips_root_memory_and_skills(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        project_root = root / "domains" / "work" / "projects" / "demo"
        for name in ("README.md", "AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "SKILLS.md"):
            write(project_root / name)
        write(project_root / "package.json", "{}\n")

        cwd = project_root
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))

        root_entry = next(entry for entry in context.visible_contexts if entry["label"] == "root")
        self.assertEqual(agent, "builder")
        self.assertNotIn("MEMORY.md", root_entry["files"])
        self.assertNotIn("SKILLS.md", root_entry["files"])
        self.assertIn("README.md", root_entry["files"])
        self.assertIn("STATE.md", root_entry["files"])

    def test_agent_subdirectory_keeps_agent_context_and_local_context(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        self.write_contract_files(root / "agents" / "builder", ("README.md", "AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md"))
        write(root / "agents" / "builder" / "tasks" / "README.md")

        cwd = root / "agents" / "builder" / "tasks"
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        mode = wrapper.default_mode(agent)
        context = wrapper.collect_context(root, cwd, agent, mode)

        self.assertEqual(agent, "builder")
        self.assertEqual(mode, "application")
        self.assertEqual(context.level, "local")
        labels = [entry["label"] for entry in context.visible_contexts]
        self.assertIn("agent:builder", labels)
        self.assertIn("local", labels)
        self.assertNotIn("system", labels)

    def test_project_wiki_entrypoints_are_included_in_context(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "wiki" / "index.md")
        write(root / "wiki" / "00_meta" / "Scope.md")
        project_root = root / "domains" / "learning" / "projects" / "demo"
        for name in ("README.md", "AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md"):
            write(project_root / name)
        write(project_root / "wiki" / "index.md")
        write(project_root / "wiki" / "00_meta" / "Scope.md")

        cwd = project_root
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))

        root_entry = next(entry for entry in context.visible_contexts if entry["label"] == "root")
        project_entry = next(entry for entry in context.visible_contexts if entry["label"] == "project:demo")

        self.assertIn("wiki/index.md", root_entry["files"])
        self.assertIn("wiki/00_meta/Scope.md", root_entry["files"])
        self.assertIn("domains/learning/projects/demo/wiki/index.md", project_entry["files"])
        self.assertIn("domains/learning/projects/demo/wiki/00_meta/Scope.md", project_entry["files"])

    def test_prompt_injection_respects_subcommands(self) -> None:
        self.assertEqual(wrapper.inject_args("claude", ["help"], "PROMPT"), ["help"])
        self.assertEqual(wrapper.inject_args("codex", ["review"], "PROMPT"), ["review"])
        self.assertEqual(wrapper.inject_args("gemini", ["mcp"], "PROMPT"), ["mcp"])
        self.assertEqual(wrapper.inject_args("claude", ["--help"], "PROMPT"), ["--help"])
        self.assertEqual(wrapper.inject_args("codex", ["--help"], "PROMPT"), ["--help"])
        self.assertEqual(wrapper.inject_args("gemini", ["--help"], "PROMPT"), ["--help"])
        self.assertEqual(
            wrapper.inject_args("claude", [], "PROMPT"),
            ["--append-system-prompt", "PROMPT"],
        )
        interactive_prompt = "SHORT PROMPT"
        self.assertEqual(
            wrapper.inject_args(
                "codex",
                ["--model", "gpt-5"],
                "PROMPT",
                context_path="/tmp/session.context.md",
            ),
            [
                "-c",
                (
                    'developer_instructions="ExoCortex bootstrap is authoritative for this session.\\n'
                    'Read `/tmp/session.context.md` first.\\n'
                    'Treat the listed files as a context manifest, not a mandate to open everything up front.\\n'
                    'Read only the smallest relevant subset when needed."'
                ),
                "--model",
                "gpt-5",
            ],
        )
        self.assertEqual(
            wrapper.inject_args(
                "codex",
                ["--model", "gpt-5"],
                "PROMPT",
                stdin_is_tty=False,
                context_path="/tmp/session.context.md",
            ),
            [
                "-c",
                (
                    'developer_instructions="ExoCortex bootstrap is authoritative for this session.\\n'
                    'Read `/tmp/session.context.md` first.\\n'
                    'Treat the listed files as a context manifest, not a mandate to open everything up front.\\n'
                    'Read only the smallest relevant subset when needed."'
                ),
                "--model",
                "gpt-5",
            ],
        )
        self.assertEqual(
            wrapper.inject_args("gemini", [], "PROMPT"),
            ["--prompt-interactive", "PROMPT"],
        )
        self.assertEqual(
            wrapper.inject_args("gemini", ["--model", "gemini-2.5-pro"], "PROMPT"),
            ["--prompt-interactive", "PROMPT", "--model", "gemini-2.5-pro"],
        )
        self.assertEqual(wrapper.inject_args("gemini", [], "PROMPT", stdin_is_tty=False), ["--prompt", "PROMPT"])

    def test_prompt_injection_merges_existing_user_prompts_for_codex_and_gemini(self) -> None:
        codex_args = wrapper.inject_args(
            "codex",
            ["Implement feature X"],
            "PROMPT",
            context_path="/tmp/session.context.md",
        )
        self.assertEqual(
            codex_args,
            [
                "-c",
                (
                    'developer_instructions="ExoCortex bootstrap is authoritative for this session.\\n'
                    'Read `/tmp/session.context.md` first.\\n'
                    'Treat the listed files as a context manifest, not a mandate to open everything up front.\\n'
                    'Read only the smallest relevant subset when needed."'
                ),
                "Implement feature X",
            ],
        )

        gemini_args = wrapper.inject_args("gemini", ["hello"], "PROMPT")
        self.assertEqual(gemini_args, ["PROMPT\n\nUser request:\nhello"])

        gemini_prompt_flag = wrapper.inject_args("gemini", ["-p", "hello"], "PROMPT")
        self.assertEqual(gemini_prompt_flag, ["-p", "PROMPT\n\nUser request:\nhello"])

        gemini_prompt_interactive = wrapper.inject_args("gemini", ["--prompt-interactive", "hello"], "PROMPT")
        self.assertEqual(
            gemini_prompt_interactive,
            ["--prompt-interactive", "PROMPT\n\nUser request:\nhello"],
        )

    def test_codex_status_text_from_event_formats_context_and_tokens(self) -> None:
        event = {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "model_context_window": 258400,
                    "last_token_usage": {
                        "input_tokens": 176759,
                    },
                    "total_token_usage": {
                        "total_tokens": 2271914,
                    },
                },
            },
        }

        text, window = wrapper.codex_status_text_from_event(event)

        self.assertEqual(text, "ctx~68% 177k/258k tok 2.27M")
        self.assertEqual(window, 258400)

    def test_find_codex_session_file_prefers_matching_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_dir = Path(temp_dir)
            matching = sessions_dir / "2026" / "04" / "16" / "rollout-match.jsonl"
            other = sessions_dir / "2026" / "04" / "16" / "rollout-other.jsonl"
            write(
                matching,
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "match", "cwd": "/tmp/demo"},
                    }
                )
                + "\n",
            )
            write(
                other,
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "other", "cwd": "/tmp/elsewhere"},
                    }
                )
                + "\n",
            )
            now = time.time()
            os.utime(other, (now - 5, now - 5))
            os.utime(matching, (now, now))

            with mock.patch.object(wrapper, "CODEX_SESSIONS_DIR", sessions_dir):
                found = wrapper.find_codex_session_file(Path("/tmp/demo"), int(time.time()))

            self.assertEqual(found, matching)

    def test_context_prompt_makes_exocortex_authority_explicit(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        write(root / "wiki" / "00_meta" / "Operating Contract.md", "# wiki contract\n")
        context = wrapper.Context(
            root=root,
            cwd=root,
            domain=None,
            project=None,
            active_agent="chief-of-staff",
            active_mode="conversation",
            level="root",
            visible_contexts=[{"label": "root", "path": ".", "files": ["README.md", "AGENT.md"]}],
            health_snapshot={},
            weighted_context=[],
        )

        prompt = wrapper.build_context_prompt(context)
        self.assertIn("- Authority: this bootstrap is authoritative for the session.", prompt)
        self.assertIn(f"- Scope: level=root; agent=chief-of-staff; mode=conversation; cwd={root}", prompt)
        self.assertIn("Read policy: start from the most specific relevant scope", prompt)
        self.assertIn("if native tool memory conflicts with this bootstrap", prompt)
        self.assertIn("Startup context manifest", prompt)
        self.assertIn("wiki/00_meta/Operating Contract.md` governs managed `wiki/` and `raw/` maintenance", prompt)
        self.assertNotIn("--- begin ---", prompt)
        self.assertNotIn("Compact source-backed notes", prompt)

    def test_authoritative_preload_prioritizes_active_scope_then_system_then_root(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        domain_root = root / "domains" / "learning"
        project_root = domain_root / "projects" / "demo"
        for name in ("README.md", "AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "SKILLS.md", "DECISION RULES.md"):
            write(domain_root / name, f"domain {name}\n")
            write(project_root / name, f"project {name}\n")
        write(project_root / "wiki" / "index.md", "project wiki\n")
        write(project_root / "wiki" / "00_meta" / "Scope.md", "project scope\n")
        write(root / "wiki" / "index.md", "root wiki\n")
        write(root / "wiki" / "00_meta" / "Scope.md", "root scope\n")

        cwd = project_root
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))
        preload = wrapper.load_authoritative_preload(context)

        paths = [item.path for item in preload.files]
        self.assertEqual(paths[0], "domains/learning/projects/demo/README.md")
        self.assertIn("domains/learning/projects/demo/wiki/index.md", paths)
        self.assertIn("system/README.md", paths)
        self.assertIn("README.md", paths)
        self.assertLess(paths.index("system/README.md"), paths.index("README.md"))

    def test_authoritative_preload_prioritizes_domain_then_system_then_root(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        domain_root = root / "domains" / "writing"
        self.write_contract_files(domain_root)

        cwd = domain_root
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))
        preload = wrapper.load_authoritative_preload(context)

        paths = [item.path for item in preload.files]
        self.assertEqual(paths[0], "domains/writing/README.md")
        self.assertIn("system/README.md", paths)
        self.assertIn("README.md", paths)
        self.assertLess(paths.index("system/README.md"), paths.index("README.md"))

    def test_authoritative_preload_prioritizes_local_then_system_then_root(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "tools" / "wrappers" / "README.md", "local readme\n")
        write(root / "tools" / "wrappers" / "AGENT.md", "local agent\n")

        cwd = root / "tools" / "wrappers"
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))
        preload = wrapper.load_authoritative_preload(context)

        paths = [item.path for item in preload.files]
        self.assertEqual(paths[0], "tools/wrappers/README.md")
        self.assertIn("system/README.md", paths)
        self.assertIn("README.md", paths)
        self.assertLess(paths.index("system/README.md"), paths.index("README.md"))

    def test_wiki_context_routes_to_knowledge_steward_and_preloads_contract(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "wiki" / "00_meta" / "Operating Contract.md", "# wiki contract\n")
        write(root / "wiki" / "index.md", "root wiki\n")
        write(root / "wiki" / "00_meta" / "Scope.md", "root scope\n")

        cwd = root / "wiki"
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        mode = wrapper.default_mode(agent)
        context = wrapper.collect_context(root, cwd, agent, mode)
        preload = wrapper.load_authoritative_preload(context)

        self.assertEqual(agent, "knowledge-steward")
        self.assertEqual(mode, "compression")
        paths = [item.path for item in preload.files]
        self.assertIn("wiki/00_meta/Operating Contract.md", paths)
        self.assertLess(paths.index("wiki/00_meta/Operating Contract.md"), paths.index("system/README.md"))

    def test_raw_context_routes_to_knowledge_steward_and_preloads_contract(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "wiki" / "00_meta" / "Operating Contract.md", "# wiki contract\n")
        write(root / "raw" / "inbox" / "README.md", "inbox readme\n")

        cwd = root / "raw" / "inbox"
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        mode = wrapper.default_mode(agent)
        context = wrapper.collect_context(root, cwd, agent, mode)
        preload = wrapper.load_authoritative_preload(context)

        self.assertEqual(agent, "knowledge-steward")
        self.assertEqual(mode, "compression")
        paths = [item.path for item in preload.files]
        self.assertIn("wiki/00_meta/Operating Contract.md", paths)

    def test_request_text_can_select_reviewer_role(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        project_root = root / "domains" / "work" / "projects" / "demo"
        self.write_contract_files(root / "domains" / "work")
        self.write_contract_files(project_root)
        write(project_root / "package.json", "{}\n")

        request = wrapper.user_request_from_args("codex", ["review this code for regressions"])
        agent = wrapper.default_agent(
            *wrapper.detect_domain_project(root, project_root),
            project_root,
            root,
            request_text=request,
        )

        self.assertEqual(agent, "reviewer")

    def test_surface_now_file_preloads_when_nonempty(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "journal" / "inbox" / "surface-now.md", "# Surface Now\n\n- check this\n")

        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, root), root, root)
        context = wrapper.collect_context(root, root, agent, wrapper.default_mode(agent))
        preload = wrapper.load_authoritative_preload(context)

        paths = [item.path for item in preload.files]
        self.assertIn("journal/inbox/surface-now.md", paths)

    def test_wiki_map_included_in_preload_when_present(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "wiki-map.md", "# Wiki Map\n")

        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, root), root, root)
        context = wrapper.collect_context(root, root, agent, wrapper.default_mode(agent))
        preload = wrapper.load_authoritative_preload(context)

        paths = [item.path for item in preload.files]
        self.assertIn("wiki-map.md", paths)

    def test_wiki_map_absent_from_loaded_files_when_missing(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        # wiki-map.md not created — must not appear in loaded files (may be in missing_files, not files)
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, root), root, root)
        context = wrapper.collect_context(root, root, agent, wrapper.default_mode(agent))
        preload = wrapper.load_authoritative_preload(context)

        loaded_paths = [item.path for item in preload.files]
        self.assertNotIn("wiki-map.md", loaded_paths)

    def test_wiki_map_referenced_in_bootstrap_prompt(self) -> None:
        # wiki-map.md is a ROOT_PRELOAD_FILE — it should appear in the context
        # manifest section of the bootstrap prompt so Claude knows to read it.
        # The wrapper lists files by path (not content); Claude reads them on demand.
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "wiki-map.md", "# Wiki Map\n## topic: test\n")
        write(root / "wiki" / "00_meta" / "Operating Contract.md", "# contract\n")

        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, root), root, root)
        context = wrapper.collect_context(root, root, agent, wrapper.default_mode(agent))
        prompt = wrapper.build_context_prompt(context)

        self.assertIn("wiki-map.md", prompt)

    def test_project_context_does_not_preload_agents_contract_by_default(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "wiki" / "00_meta" / "Operating Contract.md", "# wiki contract\n")
        domain_root = root / "domains" / "work"
        project_root = domain_root / "projects" / "demo"
        self.write_contract_files(domain_root)
        self.write_contract_files(project_root)
        write(project_root / "package.json", "{}\n")

        cwd = project_root
        agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)
        context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))
        preload = wrapper.load_authoritative_preload(context)

        paths = [item.path for item in preload.files]
        self.assertNotIn("wiki/00_meta/Operating Contract.md", paths)

    def test_agent_visibility_matrix_matches_expected_surfaces(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        domain_root = root / "domains" / "work"
        project_root = domain_root / "projects" / "demo"
        self.write_contract_files(domain_root)
        self.write_contract_files(project_root)
        write(project_root / "package.json", "{}\n")
        write(project_root / "docs" / "README.md")

        cwd = project_root / "docs"
        expected_system_visibility = {
            "builder": False,
            "planning": True,
            "research": True,
            "chief-of-staff": True,
            "knowledge-steward": True,
            "life-systems": True,
        }
        for agent, sees_system in expected_system_visibility.items():
            with self.subTest(agent=agent):
                context = wrapper.collect_context(root, cwd, agent, wrapper.default_mode(agent))
                labels = [entry["label"] for entry in context.visible_contexts]
                self.assertIn("root", labels)
                self.assertIn("domain:work", labels)
                self.assertIn("project:demo", labels)
                self.assertIn("local", labels)
                if sees_system:
                    self.assertIn("system", labels)
                else:
                    self.assertNotIn("system", labels)

                root_entry = next(entry for entry in context.visible_contexts if entry["label"] == "root")
                if agent == "builder":
                    self.assertNotIn("MEMORY.md", root_entry["files"])
                    self.assertNotIn("SKILLS.md", root_entry["files"])
                else:
                    self.assertIn("MEMORY.md", root_entry["files"])
                    self.assertIn("SKILLS.md", root_entry["files"])

    def test_level_name_matrix(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        domain_root = root / "domains" / "learning"
        project_root = domain_root / "projects" / "demo"
        local_root = root / "tools" / "wrappers"
        for path in (domain_root, project_root, local_root):
            path.mkdir(parents=True, exist_ok=True)

        self.assertEqual(wrapper.level_name(None, None, root, root), "root")
        self.assertEqual(wrapper.level_name("learning", None, domain_root, root), "domain")
        self.assertEqual(wrapper.level_name("learning", "demo", project_root, root), "project")
        self.assertEqual(wrapper.level_name(None, None, local_root, root), "local")

    def test_authoritative_preload_marks_truncation_and_total_cap(self) -> None:
        context = wrapper.Context(
            root=Path("/tmp/root"),
            cwd=Path("/tmp/root"),
            domain=None,
            project=None,
            active_agent="chief-of-staff",
            active_mode="conversation",
            level="root",
            visible_contexts=[],
            health_snapshot={},
            weighted_context=[],
        )
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        context.root = root
        for name in ("README.md", "AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md"):
            write(root / name, "A" * 50)
        for name in ("README.md", "AGENT.md", "STATE.md", "DECISION RULES.md", "PERSONA CALIBRATION.md", "HEALTH STATE.md", "HEALTH RULES.md"):
            write(root / "system" / name, "B" * 50)

        preload = wrapper.load_authoritative_preload(context, per_file_chars=20, total_chars=65)

        self.assertTrue(preload.files[0].truncated)
        self.assertTrue(preload.hit_total_cap)
        self.assertEqual(preload.total_chars, 65)

    def test_activity_log_mode_defaults_and_overrides(self) -> None:
        stream = StringIO()
        stream.isatty = lambda: True  # type: ignore[attr-defined]
        previous_mode = os.environ.get("EXOCORTEX_CLI_LOG")
        previous_term = os.environ.get("TERM")
        try:
            os.environ.pop("EXOCORTEX_CLI_LOG", None)
            os.environ["TERM"] = "xterm-256color"
            self.assertEqual(wrapper.activity_log_mode(stream), "lines")

            os.environ["EXOCORTEX_CLI_LOG"] = "lines"
            self.assertEqual(wrapper.activity_log_mode(stream), "lines")
            os.environ["EXOCORTEX_CLI_LOG"] = "bar"
            self.assertEqual(wrapper.activity_log_mode(stream), "bar")
        finally:
            if previous_mode is None:
                os.environ.pop("EXOCORTEX_CLI_LOG", None)
            else:
                os.environ["EXOCORTEX_CLI_LOG"] = previous_mode
            if previous_term is None:
                os.environ.pop("TERM", None)
            else:
                os.environ["TERM"] = previous_term

    def test_activity_log_stream_uses_stdout_for_interactive_sessions(self) -> None:
        self.assertIs(wrapper.activity_log_stream(stdin_is_tty=True), sys.stdout)
        self.assertIs(wrapper.activity_log_stream(stdin_is_tty=False), sys.stderr)

    def test_activity_log_detail_defaults_and_overrides(self) -> None:
        previous_detail = os.environ.get("EXOCORTEX_CLI_LOG_DETAIL")
        try:
            os.environ.pop("EXOCORTEX_CLI_LOG_DETAIL", None)
            self.assertEqual(wrapper.activity_log_detail(), "inferred")

            os.environ["EXOCORTEX_CLI_LOG_DETAIL"] = "lifecycle"
            self.assertEqual(wrapper.activity_log_detail(), "lifecycle")
            os.environ["EXOCORTEX_CLI_LOG_DETAIL"] = "debug"
            self.assertEqual(wrapper.activity_log_detail(), "debug")
        finally:
            if previous_detail is None:
                os.environ.pop("EXOCORTEX_CLI_LOG_DETAIL", None)
            else:
                os.environ["EXOCORTEX_CLI_LOG_DETAIL"] = previous_detail

    def test_append_status_event_dedupes_and_caps(self) -> None:
        manifest = {"status_events": []}

        self.assertTrue(wrapper.append_status_event(manifest, "boot", "lifecycle", "starting up"))
        self.assertFalse(wrapper.append_status_event(manifest, "boot", "lifecycle", "starting up"))

        for index in range(wrapper.MAX_STATUS_EVENTS + 5):
            wrapper.append_status_event(manifest, f"phase-{index}", "inferred", f"message {index}")

        self.assertEqual(len(manifest["status_events"]), wrapper.MAX_STATUS_EVENTS)
        self.assertEqual(manifest["status_events"][0]["phase"], "phase-5")

    def test_activity_reporter_lines_and_bar_modes(self) -> None:
        lines_stream = StringIO()
        reporter = wrapper.ActivityReporter("lines", "inferred", lines_stream)

        self.assertTrue(reporter.update("boot", "resolved codex"))
        self.assertFalse(reporter.update("boot", "resolved codex"))
        reporter.finish("done", "complete")
        self.assertIn("[exo] boot: resolved codex", lines_stream.getvalue())
        self.assertIn("[exo] done: complete", lines_stream.getvalue())

        bar_stream = StringIO()
        bar_reporter = wrapper.ActivityReporter("bar", "inferred", bar_stream)
        bar_reporter.update("active", "session active")
        bar_reporter.pause()
        bar_reporter.finish("done", "complete")
        bar_output = bar_stream.getvalue()
        self.assertIn("[exo] active: session active", bar_output)
        self.assertIn("[exo] done: complete\n", bar_output)

    def test_route_and_visible_context_summaries(self) -> None:
        context = wrapper.Context(
            root=Path("/tmp/root"),
            cwd=Path("/tmp/root/domains/work/projects/demo"),
            domain="work",
            project="demo",
            active_agent="builder",
            active_mode="application",
            level="project",
            visible_contexts=[
                {"label": "root", "path": ".", "files": ["README.md", "AGENT.md"]},
                {"label": "system", "path": "system", "files": ["system/README.md"]},
                {"label": "domain:work", "path": "domains/work", "files": ["README.md"]},
                {"label": "project:demo", "path": "domains/work/projects/demo", "files": ["README.md"]},
            ],
            health_snapshot={},
            weighted_context=[],
        )

        self.assertEqual(
            wrapper.route_status_message("codex", context),
            "codex -> agent=builder; mode=application; level=project",
        )
        self.assertEqual(
            wrapper.visible_context_summary(context),
            "root, system, domain:work, project:demo",
        )
        preload_report = wrapper.PreloadReport(
            active=True,
            files=[wrapper.PreloadedContextFile("README.md", "", False, 1, 1)] * 3,
            missing_files=[],
            total_chars=3,
            hit_total_cap=False,
        )
        self.assertEqual(
            wrapper.context_surface_summary(context),
            "project, domain, system, root",
        )
        self.assertEqual(
            wrapper.startup_status_message(context, preload_report),
            "builder / application / project scope",
        )
        self.assertEqual(
            wrapper.startup_surface_message(context),
            "context areas: project, domain, system, root",
        )
        self.assertEqual(
            wrapper.startup_context_count_message(preload_report),
            "using context from 3 files",
        )

    def test_startup_loaded_file_groups_return_grouped_paths(self) -> None:
        context = wrapper.Context(
            root=Path("/tmp/root"),
            cwd=Path("/tmp/root"),
            domain=None,
            project=None,
            active_agent="chief-of-staff",
            active_mode="conversation",
            level="root",
            visible_contexts=[],
            health_snapshot={},
            weighted_context=[],
        )
        preload_report = wrapper.PreloadReport(
            active=True,
            files=[
                wrapper.PreloadedContextFile("README.md", "", False, 1, 1),
                wrapper.PreloadedContextFile("AGENT.md", "", False, 1, 1),
                wrapper.PreloadedContextFile("STATE.md", "", False, 1, 1),
                wrapper.PreloadedContextFile("system/README.md", "", False, 1, 1),
                wrapper.PreloadedContextFile("system/AGENT.md", "", False, 1, 1),
            ],
            missing_files=[],
            total_chars=5,
            hit_total_cap=False,
        )

        self.assertEqual(
            wrapper.startup_loaded_file_groups(context, preload_report),
            [
                ("root", ["README.md", "AGENT.md", "STATE.md"]),
                ("system", ["system/README.md", "system/AGENT.md"]),
            ],
        )
        self.assertIsNone(wrapper.startup_context_cap_message(preload_report))

        preload_report.hit_total_cap = True
        self.assertEqual(
            wrapper.startup_context_cap_message(preload_report),
            "lower-priority context files were skipped because the startup cap was reached",
        )

    def test_startup_loaded_file_groups_keep_wiki_separate(self) -> None:
        context = wrapper.Context(
            root=Path("/tmp/root"),
            cwd=Path("/tmp/root"),
            domain=None,
            project=None,
            active_agent="chief-of-staff",
            active_mode="conversation",
            level="root",
            visible_contexts=[],
            health_snapshot={},
            weighted_context=[],
        )
        preload_report = wrapper.PreloadReport(
            active=True,
            files=[
                wrapper.PreloadedContextFile("system/README.md", "", False, 1, 1),
                wrapper.PreloadedContextFile("README.md", "", False, 1, 1),
                wrapper.PreloadedContextFile("wiki/index.md", "", False, 1, 1),
            ],
            missing_files=[],
            total_chars=3,
            hit_total_cap=False,
        )

        self.assertEqual(
            wrapper.startup_loaded_file_groups(context, preload_report),
            [
                ("system", ["system/README.md"]),
                ("root", ["README.md"]),
                ("wiki", ["wiki/index.md"]),
            ],
        )

    def test_transcript_logger_drops_spinner_churn_from_carriage_returns(self) -> None:
        handle = StringIO()
        logger = wrapper.TranscriptLogger(handle)

        lines = logger.write("tool", b"\rWorking...\rExplored\n")
        logger.finalize()

        self.assertEqual(lines, ["Explored"])
        self.assertEqual(handle.getvalue(), "[tool] Explored\n")

    def test_lazy_bootstrap_enabled_defaults_on_and_respects_opt_out(self) -> None:
        original = os.environ.pop("EXOCORTEX_LAZY_BOOTSTRAP", None)
        try:
            self.assertTrue(wrapper.lazy_bootstrap_enabled())
            os.environ["EXOCORTEX_LAZY_BOOTSTRAP"] = "0"
            self.assertFalse(wrapper.lazy_bootstrap_enabled())
            os.environ["EXOCORTEX_LAZY_BOOTSTRAP"] = "1"
            self.assertTrue(wrapper.lazy_bootstrap_enabled())
        finally:
            os.environ.pop("EXOCORTEX_LAZY_BOOTSTRAP", None)
            if original is not None:
                os.environ["EXOCORTEX_LAZY_BOOTSTRAP"] = original

    def test_load_authoritative_preload_skips_reads_when_lazy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "system").mkdir()
            (root / "wiki" / "00_meta").mkdir(parents=True)
            (root / "AGENT.md").write_text("a" * 500, encoding="utf-8")
            (root / "system" / "AGENT.md").write_text("b" * 200, encoding="utf-8")
            (root / "wiki" / "index.md").write_text("c" * 80, encoding="utf-8")
            ctx = wrapper.Context(
                root=root,
                cwd=root,
                level="root",
                domain=None,
                project=None,
                active_agent="chief-of-staff",
                active_mode="conversation",
                visible_contexts=[],
                health_snapshot={},
                weighted_context=[],
            )

            real_read_text = Path.read_text
            calls: list[Path] = []

            def tracking_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
                calls.append(self)
                return real_read_text(self, *args, **kwargs)

            with mock.patch.dict(os.environ, {"EXOCORTEX_LAZY_BOOTSTRAP": "1"}, clear=False), \
                 mock.patch.object(Path, "read_text", tracking_read_text):
                report = wrapper.load_authoritative_preload(ctx)
            self.assertTrue(report.active)
            preload_reads = [p for p in calls if p.suffix == ".md"]
            self.assertEqual(
                preload_reads,
                [],
                f"lazy bootstrap should skip per-file read_text, got: {preload_reads}",
            )
            self.assertGreater(report.total_chars, 0)

    def test_capture_strategy_defaults_to_claude_jsonl_for_claude(self) -> None:
        original = os.environ.pop("EXOCORTEX_CAPTURE", None)
        try:
            self.assertEqual(wrapper.select_capture_strategy("claude").name, "claude-jsonl")
            self.assertFalse(wrapper.select_capture_strategy("claude").requires_pty_tee)
            # Non-Claude tools always use PTY-tee regardless of the default.
            self.assertEqual(wrapper.select_capture_strategy("codex").name, "pty-tee")
            self.assertEqual(wrapper.select_capture_strategy("gemini").name, "pty-tee")
        finally:
            if original is not None:
                os.environ["EXOCORTEX_CAPTURE"] = original

    def test_capture_strategy_pty_tee_override_forces_legacy(self) -> None:
        original = os.environ.get("EXOCORTEX_CAPTURE")
        os.environ["EXOCORTEX_CAPTURE"] = "pty-tee"
        try:
            self.assertEqual(wrapper.select_capture_strategy("claude").name, "pty-tee")
            self.assertEqual(wrapper.select_capture_strategy("codex").name, "pty-tee")
        finally:
            if original is None:
                os.environ.pop("EXOCORTEX_CAPTURE", None)
            else:
                os.environ["EXOCORTEX_CAPTURE"] = original

    def test_claude_project_slug_for_cwd_matches_claude_codes_format(self) -> None:
        self.assertEqual(
            wrapper.claude_project_slug_for_cwd(Path("/path/to/exocortex")),
            "-path-to-exocortex",
        )

    def test_transcript_logger_drops_writes_when_handle_is_none(self) -> None:
        logger = wrapper.TranscriptLogger(None)
        # Lines should still be extracted for status inference even though
        # nothing is persisted to disk.
        lines = logger.write("tool", b"Explored README.md\n")
        logger.finalize()
        self.assertEqual(lines, ["Explored README.md"])

    def test_find_claude_session_jsonl_returns_recent_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cwd = tmp_path / "project"
            cwd.mkdir()
            slug_dir = tmp_path / "fake_claude" / wrapper.claude_project_slug_for_cwd(cwd)
            slug_dir.mkdir(parents=True)
            target = slug_dir / "abc.jsonl"
            target.write_text("{}\n", encoding="utf-8")
            target_mtime = time.time()
            os.utime(target, (target_mtime, target_mtime))
            with mock.patch.object(wrapper, "CLAUDE_PROJECTS_DIR", tmp_path / "fake_claude"):
                found = wrapper.find_claude_session_jsonl(cwd, int(target_mtime - 1))
                self.assertEqual(found, target)
                # Started long after file mtime → should drop the candidate.
                self.assertIsNone(
                    wrapper.find_claude_session_jsonl(cwd, int(target_mtime + 600))
                )

    def test_parse_claude_jsonl_transcript_yields_user_and_tool_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            events = [
                {"type": "user", "message": {"role": "user", "content": "hello"}},
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "internal"},
                            {"type": "text", "text": "hi there"},
                        ],
                    },
                },
                {"type": "queue-operation"},
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "content": "noise"},
                        ],
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
            text, entries = worker.parse_claude_jsonl_transcript(path)
        self.assertEqual(
            entries,
            [
                {"role": "user", "text": "hello"},
                {"role": "tool", "text": "hi there"},
            ],
        )
        self.assertIn("[user] hello", text)
        self.assertIn("[tool] hi there", text)

    def test_claude_session_uuid_from_jsonl_only_accepts_uuid_filenames(self) -> None:
        self.assertEqual(
            worker.claude_session_uuid_from_jsonl(
                Path("/tmp/abcdef12-3456-7890-abcd-ef1234567890.jsonl")
            ),
            "abcdef12-3456-7890-abcd-ef1234567890",
        )
        self.assertIsNone(worker.claude_session_uuid_from_jsonl(Path("/tmp/notes.jsonl")))

    def _seed_claude_mem_db(self, db_path: Path) -> tuple[str, str]:
        import sqlite3
        content_uuid = "11111111-2222-3333-4444-555555555555"
        memory_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.executescript(
            """
            CREATE TABLE sdk_sessions (
                id INTEGER PRIMARY KEY,
                content_session_id TEXT,
                memory_session_id TEXT,
                project TEXT
            );
            CREATE TABLE user_prompts (
                id INTEGER PRIMARY KEY,
                content_session_id TEXT,
                prompt_number INTEGER,
                prompt_text TEXT,
                created_at_epoch INTEGER
            );
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY,
                memory_session_id TEXT,
                project TEXT,
                text TEXT,
                type TEXT,
                title TEXT,
                subtitle TEXT,
                narrative TEXT,
                created_at_epoch INTEGER
            );
            """
        )
        cur.execute(
            "INSERT INTO sdk_sessions (content_session_id, memory_session_id, project) VALUES (?, ?, ?)",
            (content_uuid, memory_uuid, "Exocortex"),
        )
        cur.executemany(
            "INSERT INTO user_prompts (content_session_id, prompt_number, prompt_text, created_at_epoch) VALUES (?, ?, ?, ?)",
            [
                (content_uuid, 1, "first prompt", 1000),
                (content_uuid, 2, "second prompt", 3000),
            ],
        )
        cur.executemany(
            "INSERT INTO observations (memory_session_id, project, text, type, title, subtitle, narrative, created_at_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (memory_uuid, "Exocortex", None, "discovery", "Found root cause", None, "narrative body", 2000),
                (memory_uuid, "Exocortex", None, "feature", "Built X", "subtitle here", None, 4000),
            ],
        )
        con.commit()
        con.close()
        return content_uuid, memory_uuid

    def test_load_claude_mem_session_merges_prompts_and_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "claude-mem.db"
            content_uuid, _ = self._seed_claude_mem_db(db_path)
            with mock.patch.object(worker, "CLAUDE_MEM_DB_PATH", db_path):
                result = worker.load_claude_mem_session(content_uuid)
        self.assertIsNotNone(result)
        text, entries = result  # type: ignore[misc]
        roles = [e["role"] for e in entries]
        # Ordered by created_at_epoch: prompt(1000), observation(2000),
        # prompt(3000), observation(4000).
        self.assertEqual(roles, ["user", "tool", "user", "tool"])
        self.assertEqual(entries[0]["text"], "first prompt")
        self.assertIn("Found root cause", entries[1]["text"])
        self.assertIn("narrative body", entries[1]["text"])
        self.assertIn("Built X", entries[3]["text"])
        self.assertIn("subtitle here", entries[3]["text"])
        self.assertIn("[user] first prompt", text)

    def test_load_claude_mem_session_returns_none_when_db_missing(self) -> None:
        with mock.patch.object(worker, "CLAUDE_MEM_DB_PATH", Path("/nonexistent/claude-mem.db")):
            self.assertIsNone(worker.load_claude_mem_session("does-not-matter"))

    def test_load_session_transcript_prefers_claude_mem_then_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Set up a fake claude-mem DB and Claude .jsonl that both reference
            # the same content_session_id, plus a PTY-tee transcript.
            db_path = tmp_path / "claude-mem.db"
            content_uuid, _ = self._seed_claude_mem_db(db_path)
            cwd = tmp_path / "project"
            cwd.mkdir()
            slug_dir = tmp_path / "fake_claude" / wrapper.claude_project_slug_for_cwd(cwd)
            slug_dir.mkdir(parents=True)
            jsonl = slug_dir / f"{content_uuid}.jsonl"
            jsonl.write_text(
                json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n",
                encoding="utf-8",
            )
            now = time.time()
            os.utime(jsonl, (now, now))
            transcript = tmp_path / "transcript.md"
            transcript.write_text("[user] from-pty\n[tool] tee\n", encoding="utf-8")
            manifest = {
                "capture_strategy": "claude-jsonl",
                "cwd": str(cwd),
                "started_at_epoch": int(now - 1),
                "transcript_path": "transcript.md",
            }
            with mock.patch.object(wrapper, "CLAUDE_PROJECTS_DIR", tmp_path / "fake_claude"), \
                 mock.patch.object(worker, "CLAUDE_MEM_DB_PATH", db_path):
                _, entries = worker.load_session_transcript(tmp_path, manifest)
        # Should have come from claude-mem, not the .jsonl or PTY-tee.
        self.assertEqual(entries[0]["text"], "first prompt")
        self.assertEqual(entries[-1]["text"].splitlines()[0], "[feature] Built X")

    def test_load_session_transcript_falls_back_when_jsonl_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "transcript.md"
            transcript.write_text("[user] hi\n[tool] hello\n", encoding="utf-8")
            manifest = {
                "capture_strategy": "claude-jsonl",
                "cwd": str(root / "missing-project"),
                "started_at_epoch": int(time.time()),
                "transcript_path": "transcript.md",
            }
            with mock.patch.object(wrapper, "CLAUDE_PROJECTS_DIR", root / "no-claude-here"):
                text, entries = worker.load_session_transcript(root, manifest)
        self.assertEqual(
            [(e["role"], e["text"]) for e in entries],
            [("user", "hi"), ("tool", "hello")],
        )
        self.assertIn("[user] hi", text)

    def test_async_io_pipeline_runs_logger_writes_off_hot_path(self) -> None:
        handle = StringIO()
        logger = wrapper.TranscriptLogger(handle)
        reporter = wrapper.ActivityReporter("off", "lifecycle", stream=StringIO())
        manifest: dict = {"events": []}
        pipeline = wrapper.AsyncIOPipeline(logger, "claude", reporter, manifest)
        try:
            pipeline.enqueue("user", b"hello\n")
            pipeline.enqueue("tool", b"Explored README.md\n")
        finally:
            pipeline.shutdown()

        text = handle.getvalue()
        self.assertIn("[user] hello", text)
        self.assertIn("[tool] Explored README.md", text)

    def test_async_io_pipeline_drops_empty_payloads(self) -> None:
        handle = StringIO()
        logger = wrapper.TranscriptLogger(handle)
        reporter = wrapper.ActivityReporter("off", "lifecycle", stream=StringIO())
        pipeline = wrapper.AsyncIOPipeline(logger, "claude", reporter, {"events": []})
        try:
            pipeline.enqueue("user", b"")
        finally:
            pipeline.shutdown()
        self.assertEqual(handle.getvalue(), "")

    def test_async_io_pipeline_survives_logger_exceptions(self) -> None:
        class ExplodingLogger:
            def __init__(self) -> None:
                self.calls = 0

            def write(self, role: str, data: bytes) -> list[str]:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("disk full")
                return ["Explored"]

        logger = ExplodingLogger()
        reporter = wrapper.ActivityReporter("off", "lifecycle", stream=StringIO())
        pipeline = wrapper.AsyncIOPipeline(logger, "claude", reporter, {"events": []})
        try:
            pipeline.enqueue("tool", b"first\n")
            pipeline.enqueue("tool", b"second\n")
        finally:
            pipeline.shutdown()
        self.assertEqual(logger.calls, 2)

    def test_fast_input_enabled_defaults_on_and_respects_opt_out(self) -> None:
        original = os.environ.pop("EXOCORTEX_FAST_INPUT", None)
        try:
            self.assertTrue(wrapper.fast_input_enabled())
            os.environ["EXOCORTEX_FAST_INPUT"] = "0"
            self.assertFalse(wrapper.fast_input_enabled())
            os.environ["EXOCORTEX_FAST_INPUT"] = "1"
            self.assertTrue(wrapper.fast_input_enabled())
        finally:
            os.environ.pop("EXOCORTEX_FAST_INPUT", None)
            if original is not None:
                os.environ["EXOCORTEX_FAST_INPUT"] = original

    def test_activity_reporter_pause_and_update_are_thread_safe(self) -> None:
        import threading as _threading

        stream = StringIO()
        reporter = wrapper.ActivityReporter("bar", "verbose", stream=stream)
        reporter.update("running", "starting up")

        errors: list[BaseException] = []

        def hammer_pause() -> None:
            try:
                for _ in range(200):
                    reporter.pause()
                    reporter.resume()
            except BaseException as exc:
                errors.append(exc)

        def hammer_update() -> None:
            try:
                for i in range(200):
                    reporter.update("running", f"tick {i}")
            except BaseException as exc:
                errors.append(exc)

        threads = [
            _threading.Thread(target=hammer_pause),
            _threading.Thread(target=hammer_update),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertTrue(all(not t.is_alive() for t in threads))

    def test_terminal_noise_filter_strips_bracketed_paste_and_focus_sequences(self) -> None:
        filt = wrapper.TerminalNoiseFilter()
        cleaned = filt.feed(b"\x1b[200~hello\x1b[201~\x1b[I\x1b[O")
        self.assertEqual(cleaned, b"hello")

    def test_filter_terminal_input_does_not_treat_filtered_control_bytes_as_eof(self) -> None:
        filt = wrapper.TerminalNoiseFilter()
        cleaned, closed = wrapper.filter_terminal_input(filt, b"\x1b[I")
        self.assertEqual(cleaned, b"")
        self.assertFalse(closed)

        cleaned, closed = wrapper.filter_terminal_input(filt, b"hello")
        self.assertEqual(cleaned, b"hello")
        self.assertFalse(closed)

        cleaned, closed = wrapper.filter_terminal_input(filt, b"")
        self.assertEqual(cleaned, b"")
        self.assertTrue(closed)

    def test_classify_activity_line_maps_codex_structured_updates(self) -> None:
        self.assertEqual(
            wrapper.classify_activity_line("codex", "  └ Read SKILLS.md, MEMORY.md"),
            ("exploring", "Read SKILLS.md, MEMORY.md"),
        )
        self.assertEqual(
            wrapper.classify_activity_line("codex", "• Edited 2 files (+7 -3)"),
            ("editing", "Edited 2 files (+7 -3)"),
        )
        self.assertEqual(
            wrapper.classify_activity_line("codex", "• Ran npm run build"),
            ("testing", "npm run build"),
        )
        self.assertEqual(
            wrapper.classify_activity_line("codex", "• Waited for background terminal"),
            ("waiting", "Waited for background terminal"),
        )
        self.assertIsNone(
            wrapper.classify_activity_line(
                "codex",
                "read when navigating the root wiki. Keep it compact, current, and useful as a map.",
            )
        )

    def test_report_inferred_activity_suppresses_codex_default_wrapper_echo(self) -> None:
        stream = StringIO()
        reporter = wrapper.ActivityReporter("lines", "inferred", stream)
        manifest = {"status_events": []}

        wrapper.report_inferred_activity("codex", reporter, manifest, "exploring", "Read README.md")
        wrapper.report_inferred_activity("codex", reporter, manifest, "editing", "Edited 2 files")

        self.assertEqual(stream.getvalue(), "")
        self.assertEqual(manifest["status_events"], [])

    def test_report_inferred_activity_only_emits_phase_transitions_by_default_for_non_codex(self) -> None:
        stream = StringIO()
        reporter = wrapper.ActivityReporter("lines", "inferred", stream)
        manifest = {"status_events": []}

        wrapper.report_inferred_activity("claude", reporter, manifest, "exploring", "Read README.md")
        wrapper.report_inferred_activity("claude", reporter, manifest, "exploring", "Read STATE.md")
        wrapper.report_inferred_activity("claude", reporter, manifest, "editing", "Edited 2 files")

        output = stream.getvalue()
        self.assertIn("[exo] exploring: exploring", output)
        self.assertNotIn("Read STATE.md", output)
        self.assertIn("[exo] editing: editing", output)

    def test_report_inferred_activity_keeps_details_in_verbose_mode(self) -> None:
        stream = StringIO()
        reporter = wrapper.ActivityReporter("lines", "verbose", stream)
        manifest = {"status_events": []}

        wrapper.report_inferred_activity("codex", reporter, manifest, "exploring", "Read README.md")

        self.assertIn("[exo] exploring: Read README.md", stream.getvalue())

    def test_parse_worker_progress(self) -> None:
        self.assertEqual(
            wrapper.parse_worker_progress("EXOCORTEX_PROGRESS|weekly|building weekly synthesis"),
            ("weekly", "building weekly synthesis"),
        )
        self.assertIsNone(wrapper.parse_worker_progress("ordinary output"))

    def test_run_session_worker_records_progress_and_verbose_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")
            worker_script = root / "fake_worker.py"
            worker_script.write_text(
                "\n".join(
                    [
                        "import os",
                        "import sys",
                        "assert os.environ.get('EXOCORTEX_PROGRESS') == '1'",
                        "print('EXOCORTEX_PROGRESS|summarizing|summarizing session')",
                        "print('ordinary worker output')",
                        "raise SystemExit(0)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stream = StringIO()
            reporter = wrapper.ActivityReporter("lines", "debug", stream)
            manifest = {"status_events": []}

            result = wrapper.run_session_worker(root, worker_script, manifest_path, reporter, manifest)

            self.assertEqual(result, 0)
            self.assertTrue(any(event["phase"] == "summarizing" for event in manifest["status_events"]))
            output = stream.getvalue()
            self.assertIn("[exo] summarizing: summarizing session", output)
            self.assertIn("[exo] postprocess: ordinary worker output", output)

    def test_context_prompt_renders_compact_reuse_and_health_sections(self) -> None:
        context = wrapper.Context(
            root=Path("/tmp/root"),
            cwd=Path("/tmp/root"),
            domain=None,
            project=None,
            active_agent="chief-of-staff",
            active_mode="conversation",
            level="root",
            visible_contexts=[{"label": "root", "path": ".", "files": ["README.md", "AGENT.md"]}],
            health_snapshot={
                "energy_now": "medium",
                "stress_load_now": "low",
                "cognitive_readiness_now": "medium",
                "confidence": "low",
                "response_pacing": "normal",
                "question_load": "normal",
                "scope_bias": "normal",
                "tone": "reflective",
                "should_ask_checkin": "yes",
            },
            weighted_context=[
                {
                    "candidate_type": "memory",
                    "text": "-c, --config <key=value>",
                    "evidence_count": 13,
                    "signal_ladder": "trusted_durable_signal",
                    "score": 161.0,
                },
                {
                    "candidate_type": "self_model",
                    "text": "Prefer direct, concrete language over vague reassurance.",
                    "evidence_count": 14,
                    "signal_ladder": "trusted_durable_signal",
                    "score": 171.0,
                },
            ],
        )

        prompt = wrapper.build_context_prompt(context)
        self.assertIn("- Reusable context:", prompt)
        self.assertIn("reuse: self_model -> Prefer direct, concrete language over vague reassurance.", prompt)
        self.assertNotIn("-c, --config <key=value>", prompt)
        self.assertIn("- Health summary:", prompt)
        self.assertIn("health: energy=medium, stress=low, readiness=medium, confidence=low", prompt)
        self.assertIn("health: pacing=normal, questions=normal, scope=normal, tone=reflective", prompt)
        self.assertIn("Should I keep this tight and concrete", prompt)

    def test_health_checkin_is_suppressed_for_builder_and_compression_modes(self) -> None:
        builder_context = wrapper.Context(
            root=Path("/tmp/root"),
            cwd=Path("/tmp/root"),
            domain=None,
            project=None,
            active_agent="builder",
            active_mode="application",
            level="root",
            visible_contexts=[],
            health_snapshot={
                "confidence": "low",
                "should_ask_checkin": "yes",
                "energy_now": "low",
            },
            weighted_context=[],
        )
        knowledge_context = wrapper.Context(
            root=Path("/tmp/root"),
            cwd=Path("/tmp/root/wiki"),
            domain=None,
            project=None,
            active_agent="knowledge-steward",
            active_mode="compression",
            level="local",
            visible_contexts=[],
            health_snapshot={
                "confidence": "low",
                "should_ask_checkin": "yes",
                "energy_now": "low",
            },
            weighted_context=[],
        )

        self.assertIsNone(wrapper.health_checkin_guidance(builder_context))
        self.assertIsNone(wrapper.health_checkin_guidance(knowledge_context))

    def test_doctor_reports_preload_for_current_directory(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)
        write(root / "wiki" / "index.md", "root wiki\n")
        write(root / "wiki" / "00_meta" / "Scope.md", "root scope\n")

        report = doctor.preload_report_for_cwd(root, root)

        self.assertTrue(report.active)
        self.assertIn("README.md", report.files)
        self.assertIn("system/README.md", report.files)

    def test_doctor_path_and_resolution_helpers(self) -> None:
        wrapper_bin = Path("/tmp/exocortex/tools/wrappers/bin")
        wrapper_path = wrapper_bin / "codex"

        self.assertTrue(doctor.path_contains_entry(f"/usr/bin:{wrapper_bin}:/bin", wrapper_bin))
        self.assertFalse(doctor.path_contains_entry("/usr/bin:/bin", wrapper_bin))
        self.assertTrue(doctor.current_resolution_is_wrapper(str(wrapper_path), wrapper_path))
        self.assertFalse(doctor.current_resolution_is_wrapper("/usr/bin/codex", wrapper_path))

    def test_doctor_shell_resolution_accepts_shell_function_when_path_is_wrapped(self) -> None:
        wrapper_path = Path("/tmp/exocortex/tools/wrappers/bin/codex")
        shell_output = "codex is a shell function from ~/.zshrc"

        self.assertTrue(doctor.shell_resolution_is_wrapper(shell_output, wrapper_path, True))
        self.assertFalse(doctor.shell_resolution_is_wrapper(shell_output, wrapper_path, False))
        self.assertTrue(
            doctor.shell_resolution_is_wrapper(
                f"codex is {wrapper_path}",
                wrapper_path,
                False,
            )
        )

    def test_summary_ignores_bootstrap_and_health_placeholders(self) -> None:
        manifest = {
            "cwd": "/tmp/demo",
            "active_agent": "chief-of-staff",
            "active_mode": "conversation",
            "health_snapshot": {"confidence": "low"},
        }
        transcript = """# Session Transcript

## Stream

[tool] ExoCortex context bootstrap:
[tool] - Active level: root
[tool] - Health overlay for this session:
[tool] - sleep_status: unknown
[user] Please review this project.
[tool] Updated tools/wrappers/exocortex_wrapper.py to include local context.
[tool] How should we handle transcript capture?
[tool] Next: add automated tests later.
"""
        entries = worker.extract_transcript_entries(transcript)
        data = worker.heuristic_summary_data(manifest, entries)

        self.assertIn(
            "Updated tools/wrappers/exocortex_wrapper.py to include local context.",
            data["completed_tasks"],
        )
        self.assertIn("How should we handle transcript capture?", data["open_questions"])
        self.assertNotIn("- sleep_status: unknown", data["open_questions"])
        self.assertTrue(all("bootstrap" not in signal.lower() for signal in data["signals"]))

    def test_summary_extracts_inferred_intent_candidates(self) -> None:
        manifest = {
            "cwd": "/tmp/demo",
            "active_agent": "chief-of-staff",
            "active_mode": "conversation",
            "health_snapshot": {},
        }
        transcript = """# Session Transcript

## Stream

[user] We will also create automations through cron jobs, don't worry.
[tool] The clean future architecture is skills plus automation.
"""
        entries = worker.extract_transcript_entries(transcript)
        data = worker.heuristic_summary_data(manifest, entries)

        self.assertIn(
            "We will also create automations through cron jobs, don't worry.",
            data["intent_candidates"],
        )

    def test_summary_ignores_exocortex_status_lines(self) -> None:
        manifest = {
            "cwd": "/tmp/demo",
            "active_agent": "chief-of-staff",
            "active_mode": "conversation",
            "health_snapshot": {},
        }
        transcript = """# Session Transcript

## Stream

[tool] [exo] exploring: Read README.md
[tool] Updated tools/wrappers/exocortex_wrapper.py to include local context.
"""
        entries = worker.extract_transcript_entries(transcript)
        data = worker.heuristic_summary_data(manifest, entries)

        self.assertNotIn("[exo] exploring: Read README.md", data["signals"])
        self.assertIn(
            "Updated tools/wrappers/exocortex_wrapper.py to include local context.",
            data["completed_tasks"],
        )

    def test_worker_emit_progress_only_when_enabled(self) -> None:
        previous_progress = os.environ.get("EXOCORTEX_PROGRESS")
        try:
            os.environ.pop("EXOCORTEX_PROGRESS", None)
            silent = StringIO()
            with contextlib.redirect_stderr(silent):
                worker.emit_progress("summarizing", "summarizing session")
            self.assertEqual(silent.getvalue(), "")

            os.environ["EXOCORTEX_PROGRESS"] = "1"
            active = StringIO()
            with contextlib.redirect_stderr(active):
                worker.emit_progress("summarizing", "summarizing session")
            self.assertIn("EXOCORTEX_PROGRESS|summarizing|summarizing session", active.getvalue())
        finally:
            if previous_progress is None:
                os.environ.pop("EXOCORTEX_PROGRESS", None)
            else:
                os.environ["EXOCORTEX_PROGRESS"] = previous_progress

    def test_summarize_session_falls_back_to_codex_after_claude_failure(self) -> None:
        manifest = {
            "cwd": "/tmp/demo",
            "active_agent": "builder",
            "active_mode": "application",
            "health_snapshot": {},
        }
        transcript = "# Session Transcript\n\n## Stream\n[user] fix the wrapper fallback\n[tool] updated process_session.py\n"
        entries = worker.extract_transcript_entries(transcript)
        codex_payload = {
            "summary": "Codex completed the summary.",
            "completed_tasks": ["updated process_session.py"],
            "decisions": [],
            "open_questions": [],
            "follow_ups": [],
            "signals": [],
            "health_signals": [],
            "confidence": "medium",
            "rationale": "",
            "memory_candidates": [],
            "workflow_candidates": [],
            "skill_candidates": [],
            "decision_rule_candidates": [],
            "intent_candidates": [],
            "self_model_candidates": [],
            "persona_candidates": [],
            "question_template_candidates": [],
            "what_mattered": [],
            "repeated_patterns": [],
            "model_updates": [],
            "easier_next_time": [],
        }

        with mock.patch.dict(os.environ, {"EXOCORTEX_SUMMARIZER_PROVIDER": "claude"}, clear=False), \
             mock.patch.object(worker, "call_claude_summarizer", side_effect=RuntimeError("session closed")), \
             mock.patch.object(worker, "call_codex_summarizer", return_value=codex_payload):
            data = worker.summarize_session(Path("/tmp/root"), manifest, transcript, entries, "context")

        self.assertEqual(data["summary"], "Codex completed the summary.")
        self.assertIn("Claude summarizer failed", data["rationale"])
        self.assertIn("session closed", data["rationale"])

    def test_summarize_session_suppresses_candidates_when_models_fail(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        manifest = {
            "session_id": "s-fail",
            "cwd": "/tmp/demo",
            "active_agent": "chief-of-staff",
            "active_mode": "conversation",
            "started_at": "2026-04-12T10:00:00+00:00",
            "health_snapshot": {},
        }
        transcript = "# Session Transcript\n\n## Stream\n[user] I prefer direct answers.\n"
        entries = worker.extract_transcript_entries(transcript)

        with mock.patch.dict(os.environ, {"EXOCORTEX_SUMMARIZER_PROVIDER": "claude"}, clear=False), \
             mock.patch.object(worker, "call_claude_summarizer", side_effect=RuntimeError("closed")), \
             mock.patch.object(worker, "call_codex_summarizer", side_effect=RuntimeError("offline")):
            data = worker.summarize_session(root, manifest, transcript, entries, "context")

        self.assertEqual(data["candidate_source"], worker.NO_CANDIDATE_SOURCE)
        self.assertEqual(data["memory_candidates"], [])
        self.assertEqual(worker.build_candidate_records(manifest, data), [])
        errors = (root / "journal" / "inbox" / "synthesis-errors.md").read_text(encoding="utf-8")
        self.assertIn("no promotion candidates were written", errors)

    def test_update_project_state_falls_back_to_codex_after_claude_failure(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        self.write_contract_files(root / "domains" / "work")
        project_root = root / "domains" / "work" / "projects" / "demo"
        self.write_contract_files(project_root)
        state_path = project_root / "STATE.md"
        state_path.write_text("# Current state file with enough content\n\n- item\n" * 5, encoding="utf-8")
        manifest = {"domain": "work", "project": "demo"}
        data = {
            "summary": "done",
            "completed_tasks": ["finished fallback"],
            "decisions": [],
            "open_questions": [],
            "follow_ups": [],
        }

        def codex_update(_root: Path, _manifest: dict[str, Any], path: Path, _data: dict[str, Any]) -> None:
            path.write_text("# Updated by Codex\n\n- item\n" * 5, encoding="utf-8")

        with mock.patch.object(worker, "call_claude_state_updater", side_effect=RuntimeError("session closed")), \
             mock.patch.object(worker, "call_codex_state_updater", side_effect=codex_update) as codex_mock, \
             mock.patch.object(sys, "stderr", StringIO()), \
             mock.patch.object(worker, "heuristic_state_append") as heuristic_mock:
            worker.update_project_state(root, manifest, data, "claude")

        self.assertTrue(state_path.read_text(encoding="utf-8").startswith("# Updated by Codex"))
        codex_mock.assert_called_once()
        heuristic_mock.assert_not_called()

    def test_append_locked_once_is_race_safe_for_duplicate_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "daily.md"
            block = "## Entry\n\n- session_id: `race`\n"
            processes = [
                self.append_locked_once_via_subprocess(path, block, "race")
                for _ in range(6)
            ]
            for proc in processes:
                self.assertEqual(proc.wait(timeout=5), 0)

            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("- session_id: `race`"), 1)

    def test_append_locked_once_is_race_safe_for_distinct_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "daily.md"
            session_ids = [f"s{index}" for index in range(6)]
            processes = [
                self.append_locked_once_via_subprocess(path, f"## Entry\n\n- session_id: `{session_id}`\n", session_id)
                for session_id in session_ids
            ]
            for proc in processes:
                self.assertEqual(proc.wait(timeout=5), 0)

            text = path.read_text(encoding="utf-8")
            for session_id in session_ids:
                self.assertIn(f"- session_id: `{session_id}`", text)

    def test_wrapper_main_writes_manifest_and_returns_harness_exit_code(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        self.write_contract_files(root / "domains" / "work")
        project_root = root / "domains" / "work" / "projects" / "demo"
        self.write_contract_files(project_root)
        write(project_root / "package.json", "{}\n")

        real_cwd = Path.cwd()
        os.chdir(project_root)
        self.addCleanup(lambda: os.chdir(real_cwd))

        with mock.patch.object(wrapper, "exocortex_root", return_value=root), \
             mock.patch.object(wrapper, "find_real_binary", return_value="/usr/bin/fake-codex"), \
             mock.patch.object(wrapper, "activity_log_mode", return_value="off"), \
             mock.patch.object(wrapper, "activity_log_detail", return_value="lifecycle"), \
             mock.patch.object(wrapper, "should_capture_session", return_value=True), \
             mock.patch.object(wrapper, "run_interactive_session", return_value=7), \
             mock.patch.object(wrapper, "run_session_worker", return_value=0), \
             mock.patch.object(wrapper.uuid, "uuid4", return_value="session-1234"), \
             mock.patch.object(
                 wrapper,
                 "iso_now",
                 side_effect=[
                     "2026-04-12T10:00:00+00:00",
                     "2026-04-12T10:00:01+00:00",
                     "2026-04-12T10:00:02+00:00",
                     "2026-04-12T10:00:03+00:00",
                     "2026-04-12T10:05:00+00:00",
                     "2026-04-12T10:05:01+00:00",
                     "2026-04-12T10:05:02+00:00",
                     "2026-04-12T10:05:03+00:00",
                     "2026-04-12T10:05:04+00:00",
                     "2026-04-12T10:05:05+00:00",
                 ],
             ), \
             mock.patch.object(sys, "argv", ["exocortex_wrapper.py", "codex", "hello"]):
            exit_code = wrapper.main()

        self.assertEqual(exit_code, 7)
        manifest_path = root / "journal" / "sessions" / "2026-04-12" / "session-1234.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["tool"], "codex")
        self.assertEqual(manifest["active_agent"], "builder")
        self.assertEqual(manifest["active_mode"], "application")
        self.assertEqual(manifest["level"], "project")
        self.assertEqual(manifest["summary_status"], "complete")
        self.assertEqual(manifest["exit_code"], 7)
        self.assertTrue(any(event["phase"] == "boot" for event in manifest["status_events"]))
        self.assertTrue(any(event["phase"] == "done" for event in manifest["status_events"]))

    def test_wrapper_main_marks_failed_postprocess_but_returns_harness_exit_code(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        real_cwd = Path.cwd()
        os.chdir(root)
        self.addCleanup(lambda: os.chdir(real_cwd))

        with mock.patch.object(wrapper, "exocortex_root", return_value=root), \
             mock.patch.object(wrapper, "find_real_binary", return_value="/usr/bin/fake-codex"), \
             mock.patch.object(wrapper, "activity_log_mode", return_value="off"), \
             mock.patch.object(wrapper, "activity_log_detail", return_value="lifecycle"), \
             mock.patch.object(wrapper, "should_capture_session", return_value=True), \
             mock.patch.object(wrapper, "run_interactive_session", return_value=0), \
             mock.patch.object(wrapper, "run_session_worker", return_value=9), \
             mock.patch.object(wrapper.uuid, "uuid4", return_value="session-9999"), \
             mock.patch.object(
                 wrapper,
                 "iso_now",
                 side_effect=[
                     "2026-04-12T11:00:00+00:00",
                     "2026-04-12T11:00:01+00:00",
                     "2026-04-12T11:00:02+00:00",
                     "2026-04-12T11:00:03+00:00",
                     "2026-04-12T11:01:00+00:00",
                     "2026-04-12T11:01:01+00:00",
                     "2026-04-12T11:01:02+00:00",
                     "2026-04-12T11:01:03+00:00",
                     "2026-04-12T11:01:04+00:00",
                     "2026-04-12T11:01:05+00:00",
                 ],
             ), \
             mock.patch.object(sys, "argv", ["exocortex_wrapper.py", "codex"]):
            exit_code = wrapper.main()

        self.assertEqual(exit_code, 0)
        manifest_path = root / "journal" / "sessions" / "2026-04-12" / "session-9999.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["summary_status"], "failed")
        self.assertEqual(manifest["exit_code"], 0)
        self.assertTrue(any(event["phase"] == "failed" for event in manifest["status_events"]))

    def test_wrapper_main_emits_route_and_context_lines(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        self.write_contract_files(root / "domains" / "work")
        project_root = root / "domains" / "work" / "projects" / "demo"
        self.write_contract_files(project_root)
        write(project_root / "package.json", "{}\n")

        real_cwd = Path.cwd()
        os.chdir(project_root)
        self.addCleanup(lambda: os.chdir(real_cwd))

        stderr = StringIO()
        with mock.patch.object(wrapper, "exocortex_root", return_value=root), \
             mock.patch.object(wrapper, "find_real_binary", return_value="/usr/bin/fake-codex"), \
             mock.patch.object(wrapper, "activity_log_mode", return_value="lines"), \
             mock.patch.object(wrapper, "activity_log_detail", return_value="verbose"), \
             mock.patch.object(wrapper, "should_capture_session", return_value=True), \
             mock.patch.object(wrapper, "run_interactive_session", return_value=0), \
             mock.patch.object(wrapper, "run_session_worker", return_value=0), \
             mock.patch.object(wrapper.uuid, "uuid4", return_value="session-2222"), \
             mock.patch.object(
                 wrapper,
                 "iso_now",
                 side_effect=[
                     "2026-04-12T12:00:00+00:00",
                     "2026-04-12T12:00:01+00:00",
                     "2026-04-12T12:00:02+00:00",
                     "2026-04-12T12:00:03+00:00",
                     "2026-04-12T12:05:00+00:00",
                     "2026-04-12T12:05:01+00:00",
                     "2026-04-12T12:05:02+00:00",
                     "2026-04-12T12:05:03+00:00",
                     "2026-04-12T12:05:04+00:00",
                     "2026-04-12T12:05:05+00:00",
                 ],
             ), \
             mock.patch.object(sys, "stderr", stderr), \
             mock.patch.object(sys, "argv", ["exocortex_wrapper.py", "codex"]):
            exit_code = wrapper.main()

        self.assertEqual(exit_code, 0)
        output = stderr.getvalue()
        self.assertIn(
            "[exo] route: codex -> agent=builder; mode=application; level=project",
            output,
        )
        self.assertIn(
            "[exo] context: visible=root, domain:work, project:demo",
            output,
        )

    def test_append_locked_once_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "daily.md"
            block = "## Entry\n\n- session_id: `abc`\n"
            worker.append_locked_once(path, block, "abc")
            worker.append_locked_once(path, block, "abc")
            self.assertEqual(path.read_text(encoding="utf-8").count("session_id"), 1)

    def test_candidate_records_include_signal_metadata(self) -> None:
        manifest = {
            "session_id": "s1",
            "started_at": "2026-04-12T10:00:00+00:00",
            "ended_at": "2026-04-12T10:05:00+00:00",
            "domain": "work",
            "project": "demo",
            "level": "project",
        }
        data = {
            "confidence": "medium",
            "memory_candidates": ["User prefers direct answers."],
            "workflow_candidates": ["First run the test suite, then inspect the failing module."],
            "skill_candidates": [],
            "decision_rule_candidates": ["Default to concise framing unless depth is requested."],
            "intent_candidates": ["We will later automate recurring inbox triage through cron jobs."],
            "self_model_candidates": ["User benefits from explicit structure and clear next actions."],
            "persona_candidates": ["Direct challenge helps when priorities are fuzzy."],
            "question_template_candidates": ["What would make this easier next time?"],
            "open_questions": ["How should weekly synthesis be reviewed?"],
        }

        records = worker.build_candidate_records(manifest, data)
        memory = next(item for item in records if item["candidate_type"] == "memory")
        persona = next(item for item in records if item["candidate_type"] == "persona")
        self_model = next(item for item in records if item["candidate_type"] == "self_model")
        intent = next(item for item in records if item["candidate_type"] == "intent")

        self.assertEqual(memory["signal_ladder"], "candidate")
        self.assertEqual(memory["evidence_count"], 1)
        self.assertEqual(memory["suggested_destination"], "domains/work/projects/demo/MEMORY.md")
        self.assertEqual(persona["suggested_destination"], "system/PERSONA CALIBRATION.md")
        self.assertEqual(self_model["self_model_layer"], "stable")
        self.assertEqual(intent["suggested_destination"], "system/OPEN LOOPS.md")
        self.assertEqual(intent["artifact_kind"], "open_loop")
        self.assertEqual(intent["intent_stage"], "candidate")
        self.assertEqual(memory["tier"], "queue")
        self.assertEqual(memory["source"]["session_id"], "s1")

    def test_candidate_records_filter_cli_help_noise(self) -> None:
        manifest = {
            "session_id": "s1",
            "started_at": "2026-04-12T10:00:00+00:00",
            "ended_at": "2026-04-12T10:05:00+00:00",
            "domain": None,
            "project": None,
            "level": "root",
        }
        data = {
            "confidence": "high",
            "memory_candidates": ["-c, --config <key=value>", "Keep language concise and not sycophantic."],
            "workflow_candidates": [],
            "skill_candidates": [],
            "decision_rule_candidates": [],
            "intent_candidates": [],
            "self_model_candidates": [],
            "persona_candidates": [],
            "question_template_candidates": [],
            "open_questions": [],
        }

        records = worker.build_candidate_records(manifest, data)

        self.assertEqual([item["text"] for item in records], ["Keep language concise and not sycophantic."])

    def test_aggregated_candidates_write_review_queues_and_context_cache(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        records = [
            {
                "candidate_type": "memory",
                "text": "Prefer direct answers.",
                "normalized_key": "prefer direct answers",
                "suggested_destination": "MEMORY.md",
                "artifact_kind": "memory_note",
                "why_it_matters": "Likely durable preference.",
                "confidence": "medium",
                "first_seen": "2026-04-10T10:00:00+00:00",
                "last_seen": "2026-04-10T10:00:00+00:00",
                "source_session_ids": ["s1"],
                "domain": "work",
                "project": "demo",
                "self_model_layer": None,
            },
            {
                "candidate_type": "memory",
                "text": "Prefer direct answers.",
                "normalized_key": "prefer direct answers",
                "suggested_destination": "MEMORY.md",
                "artifact_kind": "memory_note",
                "why_it_matters": "Likely durable preference.",
                "confidence": "high",
                "first_seen": "2026-04-12T10:00:00+00:00",
                "last_seen": "2026-04-12T10:00:00+00:00",
                "source_session_ids": ["s2"],
                "domain": "work",
                "project": "demo",
                "self_model_layer": None,
            },
            {
                "candidate_type": "persona",
                "text": "Direct challenge helps when priorities are fuzzy.",
                "normalized_key": "direct challenge helps when priorities are fuzzy",
                "suggested_destination": "system/PERSONA CALIBRATION.md",
                "artifact_kind": "persona_calibration",
                "why_it_matters": "Interaction style signal.",
                "confidence": "medium",
                "first_seen": "2026-04-12T10:00:00+00:00",
                "last_seen": "2026-04-12T10:00:00+00:00",
                "source_session_ids": ["s2"],
                "domain": None,
                "project": None,
                "self_model_layer": None,
            },
            {
                "candidate_type": "intent",
                "text": "We will later automate recurring inbox triage through cron jobs.",
                "normalized_key": "we will later automate recurring inbox triage through cron jobs",
                "suggested_destination": "system/OPEN LOOPS.md",
                "artifact_kind": "open_loop",
                "why_it_matters": "Potential inferred future goal.",
                "confidence": "medium",
                "first_seen": "2026-04-12T10:00:00+00:00",
                "last_seen": "2026-04-12T10:00:00+00:00",
                "source_session_ids": ["s2"],
                "domain": None,
                "project": None,
                "self_model_layer": None,
            },
        ]

        aggregated = worker.aggregate_candidate_records(records)
        memory = next(item for item in aggregated if item["candidate_type"] == "memory")
        self.assertEqual(memory["signal_ladder"], "repeated_pattern")
        self.assertEqual(memory["evidence_count"], 2)

        worker.write_review_queues(root, aggregated)
        cache = worker.build_context_cache(aggregated)
        (root / "journal" / "inbox").mkdir(parents=True, exist_ok=True)
        (root / "journal" / "inbox" / "context-cache.json").write_text(json.dumps(cache), encoding="utf-8")

        memory_queue = (root / "journal" / "inbox" / "pending-memory.md").read_text(encoding="utf-8")
        persona_queue = (root / "journal" / "inbox" / "pending-persona.md").read_text(encoding="utf-8")
        intent_queue = (root / "journal" / "inbox" / "pending-intents.md").read_text(encoding="utf-8")
        reviewed_intents = (root / "journal" / "inbox" / "reviewed-intents.md").read_text(encoding="utf-8")
        self.assertIn("repeated_pattern", memory_queue)
        self.assertIn("Direct challenge helps when priorities are fuzzy.", persona_queue)
        self.assertIn("cron jobs", intent_queue)
        self.assertIn("confirm_open_loop", intent_queue)
        self.assertIn("- None recorded.", reviewed_intents)

        weighted = wrapper.read_weighted_context(root, "chief-of-staff", "work", "demo")
        self.assertTrue(any(item["candidate_type"] == "memory" for item in weighted))
        self.assertTrue(any(item["candidate_type"] == "persona" for item in weighted))

    def test_review_cli_accepts_candidate_and_records_decision(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        session_dir = root / "journal" / "sessions" / "2026-04-12"
        session_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "candidate_type": "memory",
            "text": "Prefer direct answers.",
            "normalized_key": "prefer direct answers",
            "suggested_destination": "MEMORY.md",
            "artifact_kind": "memory_note",
            "why_it_matters": "Likely durable preference.",
            "justification": "Likely durable preference.",
            "confidence": "high",
            "first_seen": "2026-04-12T10:00:00+00:00",
            "last_seen": "2026-04-12T10:00:00+00:00",
            "source_session_ids": ["s1"],
            "domain": None,
            "project": None,
            "self_model_layer": None,
            "tier": "queue",
            "contradicts": [],
            "related_focus": [],
        }
        (session_dir / "s1.candidates.json").write_text(
            json.dumps({"candidate_records": [record]}),
            encoding="utf-8",
        )

        exit_code = review_worker.cmd_accept(root, "direct answers", "explicitly confirmed")

        self.assertEqual(exit_code, 0)
        self.assertIn("Prefer direct answers.", (root / "MEMORY.md").read_text(encoding="utf-8"))
        self.assertIn(
            "Prefer direct answers.",
            (root / "journal" / "inbox" / "reviewed-accepted.md").read_text(encoding="utf-8"),
        )
        state = json.loads((root / "journal" / "inbox" / "review-state.json").read_text(encoding="utf-8"))
        self.assertEqual(next(iter(state["items"].values()))["action"], "accepted")

    def test_context_hygiene_reports_oversized_state_and_pending_queue(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        state_path = root / "domains" / "work" / "projects" / "demo" / "STATE.md"
        write(
            state_path,
            "# State\n\n## Current Focus\n\n- [2026-01-01] Keep going.\n\n" + ("x" * 9000),
        )
        pending = root / "journal" / "inbox" / "pending-memory.md"
        write(pending, "# Pending\n\n" + "\n".join(f"### Candidate {i}\n" for i in range(3)))

        findings = context_hygiene.run_checks(
            root,
            active_limit=8000,
            preload_limit=4000,
            pending_limit=2,
            stale_days=30,
        )
        categories = {finding.category for finding in findings}

        self.assertIn("active_context_size", categories)
        self.assertIn("pending_queue_size", categories)

    def test_context_hygiene_can_archive_surface_now(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        surface = root / "journal" / "inbox" / "surface-now.md"
        write(surface, "# Surface Now\n\n- urgent thing\n")

        archived = context_hygiene.archive_surface_now(root)

        self.assertTrue(archived)
        self.assertEqual(surface.read_text(encoding="utf-8"), "")
        self.assertIn(
            "urgent thing",
            (root / "journal" / "inbox" / "surface-now-archive.md").read_text(encoding="utf-8"),
        )

    def test_wiki_map_refresh_discovers_project_wikis(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(
            root / "wiki" / "index.md",
            "---\ntitle: Index\nstatus: active\nsummary: Root system wiki.\n---\n\n# Index\n",
        )
        project_index = root / "domains" / "work" / "projects" / "demo" / "wiki" / "index.md"
        write(
            project_index,
            "---\ntitle: Demo Wiki\nstatus: active\nsummary: Demo project knowledge.\n---\n\n# Demo Wiki\n",
        )

        content = wiki_map_maintain.refresh(root, apply=True)

        self.assertIn("## topic: Demo", content)
        self.assertIn("domains/work/projects/demo/wiki/", (root / "wiki-map.md").read_text(encoding="utf-8"))

    def test_reprocess_sessions_finds_missing_artifacts(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        manifest = root / "journal" / "sessions" / "2026-04-12" / "s1.json"
        write(manifest, "{}\n")

        missing = reprocess_sessions.missing_session_manifests(root)

        self.assertEqual(missing, [manifest])

    def test_reprocess_sessions_times_out_stuck_manifest(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        script = root / "tools" / "workers" / "process_session.py"
        write(script, "import time\ntime.sleep(5)\n")
        manifest = root / "journal" / "sessions" / "2026-04-12" / "s1.json"
        write(manifest, "{}\n")

        code = reprocess_sessions.process_manifest(root, manifest, timeout_seconds=1)

        self.assertEqual(code, 124)

    def test_raw_ingest_creates_source_note_and_moves_raw_file(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "wiki" / "index.md", "# Root Wiki\n")
        write(root / "wiki" / "log.md", "# Log\n\n")
        raw_file = root / "raw" / "inbox" / "notes" / "decision-space.md"
        write(raw_file, "# Decision Space\n\nUseful raw detail.\n")

        items = ingest_raw.discover_raw_items(root, limit=1)
        self.assertEqual(len(items), 1)

        source_note = ingest_raw.ingest_item(root, items[0])

        self.assertTrue(source_note.exists())
        self.assertFalse(raw_file.exists())
        self.assertIn("raw/processed/", source_note.read_text(encoding="utf-8"))
        self.assertIn("Source -", (root / "wiki" / "index.md").read_text(encoding="utf-8"))

    def test_context_hygiene_can_ingest_raw_files(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "wiki" / "index.md", "# Root Wiki\n")
        write(root / "raw" / "inbox" / "capture.md", "# Capture\n\nA captured note.\n")

        ingested = context_hygiene.ingest_raw(root, limit=5)

        self.assertEqual(ingested, 1)
        self.assertTrue((root / "raw" / "processed").exists())
        self.assertTrue((root / "wiki-map.md").exists())

    def test_retrieve_searches_markdown_beyond_preload(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "wiki" / "decision-space.md", "# Decision Space\n\nMap the option set before choosing.")
        write(root / "journal" / "notes.md", "# Other\n\ndecision option decision option decision option")

        hits = retrieve.search(root, "decision option", limit=3)

        self.assertEqual(hits[0].path, "wiki/decision-space.md")
        self.assertGreaterEqual(hits[0].score, 2)
        self.assertIn("option set", hits[0].excerpt)

    def test_review_cli_can_defer_and_expire_candidate(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        session_dir = root / "journal" / "sessions" / "2026-04-12"
        session_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "candidate_type": "memory",
            "text": "Prefer short answers.",
            "normalized_key": "prefer short answers",
            "suggested_destination": "MEMORY.md",
            "artifact_kind": "memory_note",
            "why_it_matters": "Likely durable preference.",
            "justification": "Likely durable preference.",
            "confidence": "medium",
            "first_seen": "2026-01-01T10:00:00+00:00",
            "last_seen": "2026-01-01T10:00:00+00:00",
            "source_session_ids": ["s1"],
            "domain": None,
            "project": None,
            "self_model_layer": None,
            "tier": "queue",
            "contradicts": [],
            "related_focus": [],
        }
        (session_dir / "s1.candidates.json").write_text(
            json.dumps({"candidate_records": [record]}),
            encoding="utf-8",
        )

        review_worker.cmd_defer(root, "short answers", "not enough evidence")
        self.assertTrue(any(item["text"] == "Prefer short answers." for item in review_worker.pending_records(root)))

        review_worker.cmd_expire(root, days=30, apply=True)

        self.assertFalse(any(item["text"] == "Prefer short answers." for item in review_worker.pending_records(root)))
        self.assertIn(
            "Prefer short answers.",
            (root / "journal" / "inbox" / "reviewed-expired.md").read_text(encoding="utf-8"),
        )

    def _write_candidate(
        self,
        root: Path,
        session_id: str,
        text: str,
        *,
        candidate_type: str = "memory",
        destination: str = "MEMORY.md",
        confidence: str = "medium",
        last_seen: str = "2026-04-12T10:00:00+00:00",
        normalized_key: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "candidate_type": candidate_type,
            "text": text,
            "normalized_key": normalized_key or text.lower().strip().rstrip("."),
            "suggested_destination": destination,
            "artifact_kind": "memory_note",
            "why_it_matters": "Likely durable preference.",
            "justification": "Likely durable preference.",
            "confidence": confidence,
            "first_seen": last_seen,
            "last_seen": last_seen,
            "source_session_ids": [session_id],
            "domain": None,
            "project": None,
            "self_model_layer": None,
            "tier": "queue",
            "contradicts": [],
            "related_focus": [],
        }
        session_dir = root / "journal" / "sessions" / "2026-04-12"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / f"{session_id}.candidates.json").write_text(
            json.dumps({"candidate_records": [record]}),
            encoding="utf-8",
        )
        return record

    def test_pending_records_dedup_collapses_same_content_across_destinations(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        # Same content surfaced under two different suggested destinations.
        self._write_candidate(root, "s1", "Prefer direct answers.", destination="MEMORY.md", confidence="low")
        self._write_candidate(
            root, "s2", "Prefer direct answers.", destination="system/MEMORY.md", confidence="high"
        )

        pending = review_worker.pending_records(root)
        matching = [r for r in pending if r["text"] == "Prefer direct answers."]
        self.assertEqual(len(matching), 1, "duplicate content must appear once after dedup")
        # The surviving record keeps the higher confidence signal.
        self.assertEqual(matching[0]["confidence"], "high")

    def test_pending_records_highest_confidence_first(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        self._write_candidate(root, "s1", "Low signal thing.", confidence="low", destination="A.md")
        self._write_candidate(root, "s2", "High signal thing.", confidence="high", destination="B.md")

        pending = review_worker.pending_records(root)
        self.assertEqual(pending[0]["text"], "High signal thing.")

    def test_batch_accept_promotes_multiple_and_logs_decisions(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        self._write_candidate(root, "s1", "First durable preference.", destination="MEMORY.md")
        self._write_candidate(root, "s2", "Second durable preference.", destination="MEMORY.md")

        exit_code = review_worker.cmd_batch(root, "accept", ["First durable", "Second durable"], note="bulk")
        self.assertEqual(exit_code, 0)

        memory = (root / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("First durable preference.", memory)
        self.assertIn("Second durable preference.", memory)

        remaining = {r["text"] for r in review_worker.pending_records(root)}
        self.assertNotIn("First durable preference.", remaining)
        self.assertNotIn("Second durable preference.", remaining)

    def test_batch_top_selects_highest_confidence(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        self._write_candidate(root, "s1", "Top ranked durable preference.", confidence="high", destination="A.md")
        self._write_candidate(root, "s2", "Second ranked durable preference.", confidence="high", destination="B.md")
        self._write_candidate(root, "s3", "Lowest ranked durable preference.", confidence="low", destination="C.md")

        review_worker.cmd_batch(root, "expire", needles=None, top=2)
        remaining = {r["text"] for r in review_worker.pending_records(root)}
        self.assertEqual(remaining, {"Lowest ranked durable preference."})

    def test_decisions_log_appends_stable_training_record(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        self._write_candidate(root, "s1", "Logged preference.", confidence="high", destination="MEMORY.md")
        review_worker.cmd_accept(root, "Logged preference", "confirmed")

        log_path = root / "journal" / "inbox" / "review-decisions.jsonl"
        self.assertTrue(log_path.exists())
        lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        for field in (
            "timestamp",
            "candidate_id",
            "content_hash",
            "candidate_type",
            "confidence",
            "decision",
            "scope",
            "suggested_destination",
        ):
            self.assertIn(field, rec)
        self.assertEqual(rec["decision"], "accept")
        self.assertEqual(rec["candidate_type"], "memory")

    def test_triage_dry_run_reports_drops_without_writing(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        old = "2026-01-01T10:00:00+00:00"
        recent = now_iso_for_test()
        self._write_candidate(root, "s1", "Stale thing one.", last_seen=old, destination="A.md")
        self._write_candidate(root, "s2", "Stale thing two.", last_seen=old, destination="B.md")
        self._write_candidate(root, "s3", "Fresh thing.", last_seen=recent, destination="C.md")

        report = review_worker.triage(root, days=30, apply=False)
        self.assertGreaterEqual(report["would_expire"], 2)
        # Dry-run writes nothing.
        self.assertFalse((root / "journal" / "inbox" / "review-decisions.jsonl").exists())
        self.assertIn("by_reason", report)
        self.assertEqual(report["resulting_pending"], report["pending_before"] - report["would_expire"])
        # Nothing actually expired.
        self.assertEqual(len(review_worker.pending_records(root)), report["pending_before"])

    def test_triage_apply_expires_stale_and_logs(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        old = "2026-01-01T10:00:00+00:00"
        self._write_candidate(root, "s1", "Stale thing one.", last_seen=old, destination="A.md")
        self._write_candidate(root, "s2", "Fresh thing.", last_seen=now_iso_for_test(), destination="C.md")

        report = review_worker.triage(root, days=30, apply=True)
        self.assertGreaterEqual(report["expired"], 1)
        remaining = {r["text"] for r in review_worker.pending_records(root)}
        self.assertNotIn("Stale thing one.", remaining)
        self.assertIn("Fresh thing.", remaining)
        self.assertTrue((root / "journal" / "inbox" / "review-decisions.jsonl").exists())

    def test_builder_weighted_context_excludes_persona_and_self_model(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        cache = {
            "global": [
                {
                    "candidate_type": "persona",
                    "text": "Challenge vague thinking early.",
                    "domains": [],
                    "projects": [],
                    "score": 10.0,
                },
                {
                    "candidate_type": "self_model",
                    "text": "User benefits from reflective framing.",
                    "domains": [],
                    "projects": [],
                    "score": 9.0,
                },
                {
                    "candidate_type": "workflow",
                    "text": "Run focused tests before broader validation.",
                    "domains": [],
                    "projects": [],
                    "score": 8.0,
                },
            ],
            "by_domain": {
                "work": [
                    {
                        "candidate_type": "memory",
                        "text": "Prefer concrete progress updates.",
                        "domains": ["work"],
                        "projects": [],
                        "score": 7.0,
                    }
                ]
            },
            "by_project": {
                "work/demo": [
                    {
                        "candidate_type": "decision_rule",
                        "text": "Prefer the smallest viable patch in existing codebases.",
                        "domains": ["work"],
                        "projects": ["demo"],
                        "score": 6.0,
                    }
                ]
            },
        }
        (root / "journal" / "inbox").mkdir(parents=True, exist_ok=True)
        (root / "journal" / "inbox" / "context-cache.json").write_text(json.dumps(cache), encoding="utf-8")

        weighted = wrapper.read_weighted_context(root, "builder", "work", "demo")

        self.assertTrue(any(item["candidate_type"] == "memory" for item in weighted))
        self.assertTrue(any(item["candidate_type"] == "workflow" for item in weighted))
        self.assertTrue(any(item["candidate_type"] == "decision_rule" for item in weighted))
        self.assertFalse(any(item["candidate_type"] == "persona" for item in weighted))
        self.assertFalse(any(item["candidate_type"] == "self_model" for item in weighted))

    def test_intent_review_promotes_and_tracks_evidence(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        records = [
            {
                "candidate_type": "intent",
                "text": "We will later automate recurring inbox triage through cron jobs.",
                "normalized_key": "we will later automate recurring inbox triage through cron jobs",
                "signal_ladder": "repeated_pattern",
                "suggested_destination": "system/OPEN LOOPS.md",
                "artifact_kind": "open_loop",
                "why_it_matters": "Potential inferred future goal.",
                "confidence": "medium",
                "first_seen": "2026-04-12T10:00:00+00:00",
                "last_seen": "2026-04-12T12:00:00+00:00",
                "evidence_count": 2,
                "status": "pending",
                "source_session_ids": ["s1", "s2"],
                "recent_evidence": [
                    "We will later automate recurring inbox triage through cron jobs.",
                    "Later we should automate recurring inbox triage through cron jobs.",
                ],
                "domains": [],
                "projects": [],
                "self_model_layer": None,
                "score": 37.0,
            }
        ]

        intent_review.append_open_loop(
            root,
            records[0],
            "Automate recurring inbox triage through cron jobs",
            "Repeated enough to become a tracked open loop.",
        )
        intent_review.record_review_decision(
            root,
            records[0],
            stage="confirmed_open_loop",
            promoted_to="system/OPEN LOOPS.md",
            review_note="Repeated enough to become a tracked open loop.",
            promotion_text="Automate recurring inbox triage through cron jobs",
        )
        worker.write_review_queues(root, records)

        open_loops = (root / "system" / "OPEN LOOPS.md").read_text(encoding="utf-8")
        pending_intents = (root / "journal" / "inbox" / "pending-intents.md").read_text(encoding="utf-8")
        reviewed_intents = (root / "journal" / "inbox" / "reviewed-intents.md").read_text(encoding="utf-8")
        state = json.loads((root / "journal" / "inbox" / "intent-review-state.json").read_text(encoding="utf-8"))

        self.assertIn("Automate recurring inbox triage through cron jobs", open_loops)
        self.assertIn("promoted_from: `inferred_intent`", open_loops)
        self.assertNotIn("We will later automate recurring inbox triage through cron jobs.", pending_intents)
        self.assertIn("confirmed_open_loop", reviewed_intents)
        self.assertEqual(len(state["items"]), 1)

        intent_review.append_priority(
            root,
            records[0],
            "Automate recurring inbox triage through cron jobs",
            "This should be part of the active implementation priorities.",
        )
        intent_review.record_review_decision(
            root,
            records[0],
            stage="priority",
            promoted_to="system/PRIORITIES.md",
            review_note="This should be part of the active implementation priorities.",
            promotion_text="Automate recurring inbox triage through cron jobs",
        )
        worker.write_review_queues(root, records)

        priorities = (root / "system" / "PRIORITIES.md").read_text(encoding="utf-8")
        reviewed_intents = (root / "journal" / "inbox" / "reviewed-intents.md").read_text(encoding="utf-8")

        self.assertIn("promoted_from: `confirmed_open_loop`", priorities)
        self.assertIn("## Priorities", reviewed_intents)

    def test_weekly_synthesis_is_generated(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        intelligence = [
            {
                "session_id": "s1",
                "started_at": "2026-04-12T10:00:00+00:00",
                "active_agent": "chief-of-staff",
                "active_mode": "conversation",
                "confidence": "medium",
                "open_questions": ["What should be easier next time?"],
                "model_updates": ["User prefers explicit structure."],
                "easier_next_time": ["Turn repeated planning steps into a checklist."],
                "candidate_records": [
                    {
                        "candidate_type": "workflow",
                        "text": "Turn repeated planning steps into a checklist.",
                        "normalized_key": "turn repeated planning steps into a checklist",
                        "suggested_destination": "WORKFLOWS.md",
                        "artifact_kind": "workflow",
                        "why_it_matters": "Reusable process.",
                        "confidence": "medium",
                        "first_seen": "2026-04-12T10:00:00+00:00",
                        "last_seen": "2026-04-12T10:00:00+00:00",
                        "source_session_ids": ["s1"],
                        "domain": None,
                        "project": None,
                        "self_model_layer": None,
                    }
                ],
            },
            {
                "session_id": "s2",
                "started_at": "2026-04-12T11:00:00+00:00",
                "active_agent": "planning",
                "active_mode": "processing",
                "confidence": "medium",
                "open_questions": [],
                "model_updates": ["User prefers explicit structure."],
                "easier_next_time": ["Turn repeated planning steps into a checklist."],
                "candidate_records": [
                    {
                        "candidate_type": "workflow",
                        "text": "Turn repeated planning steps into a checklist.",
                        "normalized_key": "turn repeated planning steps into a checklist",
                        "suggested_destination": "WORKFLOWS.md",
                        "artifact_kind": "workflow",
                        "why_it_matters": "Reusable process.",
                        "confidence": "medium",
                        "first_seen": "2026-04-12T11:00:00+00:00",
                        "last_seen": "2026-04-12T11:00:00+00:00",
                        "source_session_ids": ["s2"],
                        "domain": None,
                        "project": None,
                        "self_model_layer": None,
                    }
                ],
            },
        ]

        # Model synthesis unavailable -> weekly falls back to the mechanical body.
        with mock.patch.object(worker, "render_period_synthesis", return_value=None):
            worker.render_weekly_synthesis(root, "2026-W15", intelligence)
        weekly = (root / "journal" / "weekly" / "2026-W15.md").read_text(encoding="utf-8")
        self.assertIn("Repeated High-Signal Patterns", weekly)
        self.assertIn("Turn repeated planning steps into a checklist.", weekly)


    # --- commitment_strength / is_urgent / review_recommendation ---

    def test_commitment_strength_strong_phrases(self) -> None:
        strong = [
            "I will fix this tomorrow",
            "We'll automate the triage soon",
            "I need to refactor the wrapper",
            "We need to ship by Friday",
            "must resolve before release",
            "going to add tests",
            "plan to migrate next week",
            "commit to finishing this sprint",
        ]
        for text in strong:
            with self.subTest(text=text):
                self.assertEqual(intent_review.commitment_strength(text), "strong", text)

    def test_commitment_strength_soft_phrases(self) -> None:
        soft = [
            "maybe we could try later",
            "it might be worth exploring",
            "could be interesting",
            "",
        ]
        for text in soft:
            with self.subTest(text=text):
                self.assertEqual(intent_review.commitment_strength(text), "soft", text)

    def test_is_urgent_temporal_markers(self) -> None:
        urgent = [
            "fix this now",
            "do it next sprint",
            "needed soon",
            "finish asap",
            "this week we should",
            "today we ship",
            "deploy tomorrow",
            "urgent blocker",
            "merge by friday",
        ]
        for text in urgent:
            with self.subTest(text=text):
                self.assertTrue(intent_review.is_urgent(text), text)

    def test_is_urgent_non_urgent_phrases(self) -> None:
        not_urgent = [
            "eventually we should clean this up",
            "someday migrate to the new API",
            "in a future quarter consider",
            "",
        ]
        for text in not_urgent:
            with self.subTest(text=text):
                self.assertFalse(intent_review.is_urgent(text), text)

    def test_review_recommendation_high_confidence_confirms_open_loop(self) -> None:
        record = {
            "candidate_type": "intent",
            "text": "Explore async option",
            "confidence": "high",
            "evidence_count": 1,
            "intent_stage": "inferred_intent",
        }
        self.assertEqual(intent_review.review_recommendation(record), "confirm_open_loop")

    def test_review_recommendation_strong_commitment_confirms_open_loop(self) -> None:
        record = {
            "candidate_type": "intent",
            "text": "I will migrate the auth layer next week",
            "confidence": "low",
            "evidence_count": 1,
            "intent_stage": "inferred_intent",
        }
        self.assertEqual(intent_review.review_recommendation(record), "confirm_open_loop")

    def test_review_recommendation_repeated_evidence_confirms_open_loop(self) -> None:
        record = {
            "candidate_type": "intent",
            "text": "refactor this someday",
            "confidence": "low",
            "evidence_count": 2,
            "intent_stage": "inferred_intent",
        }
        self.assertEqual(intent_review.review_recommendation(record), "confirm_open_loop")

    def test_review_recommendation_soft_low_keeps_inferred(self) -> None:
        record = {
            "candidate_type": "intent",
            "text": "maybe explore this",
            "confidence": "low",
            "evidence_count": 1,
            "intent_stage": "inferred_intent",
        }
        self.assertEqual(intent_review.review_recommendation(record), "keep_inferred")

    def test_review_recommendation_confirmed_urgent_promotes_priority(self) -> None:
        record = {
            "candidate_type": "intent",
            "text": "fix the deploy pipeline asap",
            "confidence": "medium",
            "evidence_count": 1,
            "intent_stage": "confirmed_open_loop",
        }
        self.assertEqual(intent_review.review_recommendation(record), "promote_priority")

    def test_review_recommendation_confirmed_repeated_promotes_priority(self) -> None:
        record = {
            "candidate_type": "intent",
            "text": "add integration tests",
            "confidence": "medium",
            "evidence_count": 3,
            "intent_stage": "confirmed_open_loop",
        }
        self.assertEqual(intent_review.review_recommendation(record), "promote_priority")

    def test_review_recommendation_confirmed_not_urgent_stays_tracked(self) -> None:
        record = {
            "candidate_type": "intent",
            "text": "clean up docs eventually",
            "confidence": "medium",
            "evidence_count": 1,
            "intent_stage": "confirmed_open_loop",
        }
        self.assertEqual(intent_review.review_recommendation(record), "tracked_open_loop")

    # --- build_context_cache domain/project bucketing ---

    def _make_candidate(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "candidate_type": "memory",
            "text": "Use direct answers.",
            "normalized_key": "use direct answers",
            "artifact_kind": "memory_note",
            "signal_ladder": "candidate",
            "evidence_count": 1,
            "confidence": "medium",
            "score": 10.0,
            "suggested_destination": "MEMORY.md",
            "why_it_matters": "Style preference.",
            "domains": ["work"],
            "projects": ["demo"],
            "self_model_layer": None,
            "last_seen": "2026-04-12T10:00:00+00:00",
            "source_session_ids": ["s1"],
            "first_seen": "2026-04-12T10:00:00+00:00",
            "domain": "work",
            "project": "demo",
        }
        base.update(overrides)
        return base

    def test_build_context_cache_buckets_by_domain_and_project(self) -> None:
        records = [self._make_candidate()]
        aggregated = worker.aggregate_candidate_records(records)
        cache = worker.build_context_cache(aggregated)

        self.assertIn("work", cache["by_domain"])
        self.assertIn("work/demo", cache["by_project"])
        global_texts = [item["text"] for item in cache["global"]]
        self.assertIn("Use direct answers.", global_texts)

    def test_build_context_cache_excludes_intent_type(self) -> None:
        records = [self._make_candidate(
            candidate_type="intent",
            text="We will automate triage.",
            normalized_key="we will automate triage",
            artifact_kind="open_loop",
            suggested_destination="system/OPEN LOOPS.md",
            why_it_matters="Future goal.",
            domains=[],
            projects=[],
            domain=None,
            project=None,
        )]
        aggregated = worker.aggregate_candidate_records(records)
        cache = worker.build_context_cache(aggregated)
        self.assertEqual(cache["global"], [])

    # --- read_weighted_context scoring boosts ---

    def test_weighted_context_boosts_score_for_domain_and_project_match(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        cache = {
            "generated_at": "2026-04-12T10:00:00+00:00",
            "global": [
                {
                    "candidate_type": "memory",
                    "text": "Global tip.",
                    "signal_ladder": "candidate",
                    "evidence_count": 1,
                    "confidence": "medium",
                    "score": 5.0,
                    "suggested_destination": "MEMORY.md",
                    "why_it_matters": "tip",
                    "domains": [],
                    "projects": [],
                    "self_model_layer": None,
                    "last_seen": "2026-04-12T10:00:00+00:00",
                },
                {
                    "candidate_type": "memory",
                    "text": "Domain-specific tip.",
                    "signal_ladder": "candidate",
                    "evidence_count": 1,
                    "confidence": "medium",
                    "score": 5.0,
                    "suggested_destination": "MEMORY.md",
                    "why_it_matters": "domain tip",
                    "domains": ["work"],
                    "projects": ["demo"],
                    "self_model_layer": None,
                    "last_seen": "2026-04-12T10:00:00+00:00",
                },
            ],
            "by_domain": {},
            "by_project": {},
        }
        inbox = root / "journal" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "context-cache.json").write_text(json.dumps(cache), encoding="utf-8")

        results = wrapper.read_weighted_context(root, "chief-of-staff", "work", "demo")
        texts = [item["text"] for item in results]
        scores = {item["text"]: item["score"] for item in results}

        self.assertIn("Domain-specific tip.", texts)
        self.assertIn("Global tip.", texts)
        # Domain-specific tip gets +4 (domain match) + +6 (project match) boost
        self.assertGreater(scores["Domain-specific tip."], scores["Global tip."])

    def test_weighted_context_deduplicates_same_text(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        item = {
            "candidate_type": "memory",
            "text": "Prefer short answers.",
            "signal_ladder": "candidate",
            "evidence_count": 1,
            "confidence": "medium",
            "score": 5.0,
            "suggested_destination": "MEMORY.md",
            "why_it_matters": "style",
            "domains": ["work"],
            "projects": [],
            "self_model_layer": None,
            "last_seen": "2026-04-12T10:00:00+00:00",
        }
        cache = {
            "generated_at": "2026-04-12T10:00:00+00:00",
            "global": [item],
            "by_domain": {"work": [item]},
            "by_project": {},
        }
        inbox = root / "journal" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "context-cache.json").write_text(json.dumps(cache), encoding="utf-8")

        results = wrapper.read_weighted_context(root, "chief-of-staff", "work", None)
        texts = [item["text"] for item in results]
        # Appears in both global and by_domain but must deduplicate to one entry
        self.assertEqual(texts.count("Prefer short answers."), 1)

    def test_weighted_context_builder_excludes_persona(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        cache = {
            "generated_at": "2026-04-12T10:00:00+00:00",
            "global": [
                {
                    "candidate_type": "persona",
                    "text": "Challenge me directly.",
                    "signal_ladder": "candidate",
                    "evidence_count": 1,
                    "confidence": "medium",
                    "score": 10.0,
                    "suggested_destination": "system/PERSONA CALIBRATION.md",
                    "why_it_matters": "style",
                    "domains": [],
                    "projects": [],
                    "self_model_layer": None,
                    "last_seen": "2026-04-12T10:00:00+00:00",
                }
            ],
            "by_domain": {},
            "by_project": {},
        }
        inbox = root / "journal" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "context-cache.json").write_text(json.dumps(cache), encoding="utf-8")

        builder_results = wrapper.read_weighted_context(root, "builder", None, None)
        cos_results = wrapper.read_weighted_context(root, "chief-of-staff", None, None)

        self.assertEqual(builder_results, [])
        self.assertEqual(len(cos_results), 1)

    # --- EXOCORTEX_AGENT env var override ---

    def test_agent_env_var_overrides_default_routing(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        domain_root = root / "domains" / "work"
        self.write_contract_files(domain_root)

        cwd = domain_root
        with mock.patch.dict(os.environ, {"EXOCORTEX_AGENT": "knowledge-steward"}):
            agent = wrapper.default_agent(*wrapper.detect_domain_project(root, cwd), cwd, root)

        self.assertEqual(agent, "knowledge-steward")


class SummarizerModelPinTests(unittest.TestCase):
    """Every headless ``claude -p`` summarizer call MUST pin a model explicitly.

    The original failure: the command omitted ``--model``, so it defaulted to the
    interactive session's model (Fable 5), which was unavailable for headless
    calls and silently failed EVERY session summary — leaving the brief with no
    real content. The pre-existing tests mocked the subprocess and only checked
    JSON parsing, so a missing flag was invisible to them. These tests guard the
    command itself, which is where the bug lived."""

    def test_summarizer_flags_pin_model_effort_and_fallback(self) -> None:
        flags = worker.summarizer_flags()
        self.assertIn("--model", flags)
        self.assertTrue(flags[flags.index("--model") + 1], "model must be non-empty")
        self.assertIn("--effort", flags)
        self.assertIn("--fallback-model", flags, "a fallback keeps summaries flowing if the primary is unavailable")

    def test_headless_claude_argv_includes_bare(self) -> None:
        argv = worker.headless_claude_argv("claude", "--output-format", "json")
        self.assertEqual(argv[0], "claude")
        self.assertEqual(argv[1], "--bare", "headless summarizers must skip hooks to avoid Stop-hook cascades")
        self.assertEqual(argv[2], "-p")
        self.assertIn("--model", argv)

    def test_summarizer_command_pins_a_model(self) -> None:
        captured: dict[str, list[str]] = {}

        def fake_run(cmd, **kwargs):  # capture the command, then stop early
            captured["cmd"] = cmd
            raise RuntimeError("stop-after-capture")

        with mock.patch.object(worker, "find_real_binary", return_value="claude"), \
             mock.patch.object(worker, "summary_prompt_template", return_value="{manifest}{context}{transcript}"), \
             mock.patch.object(worker.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                worker.call_claude_summarizer(Path("/tmp"), {"x": 1}, "transcript", "context")

        cmd = captured["cmd"]
        self.assertIn("--bare", cmd, "summarizer command must skip hooks")
        self.assertIn("--model", cmd, "summarizer command must never rely on the CLI default model")
        self.assertTrue(cmd[cmd.index("--model") + 1], "pinned model must be non-empty")
        self.assertIn("--effort", cmd)


class ClaudeMemTranscriptFallbackTests(unittest.TestCase):
    """``load_claude_mem_session`` must not short-circuit the rich raw jsonl
    with a prompts-only result. When claude-mem has logged user prompts but
    not yet compressed any observations, the function must return ``None`` so
    ``load_session_transcript`` falls through to Claude's native ``.jsonl``.
    """

    def make_db(
        self,
        *,
        prompts: list[tuple[str, int]],
        observations: list[tuple[str, str, str, int]],
        link_session: bool = True,
    ) -> Path:
        import sqlite3

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = Path(temp_dir.name) / "claude-mem.db"
        content_id = "content-uuid"
        memory_id = "memory-id"
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE sdk_sessions (content_session_id TEXT, memory_session_id TEXT)"
        )
        con.execute(
            "CREATE TABLE user_prompts (content_session_id TEXT, prompt_text TEXT, "
            "prompt_number INTEGER, created_at_epoch INTEGER)"
        )
        con.execute(
            "CREATE TABLE observations (memory_session_id TEXT, type TEXT, title TEXT, "
            "subtitle TEXT, narrative TEXT, text TEXT, created_at_epoch INTEGER)"
        )
        if link_session:
            con.execute(
                "INSERT INTO sdk_sessions VALUES (?, ?)", (content_id, memory_id)
            )
        for i, (text, epoch) in enumerate(prompts):
            con.execute(
                "INSERT INTO user_prompts VALUES (?, ?, ?, ?)",
                (content_id, text, i, epoch),
            )
        for obs_type, title, narrative, epoch in observations:
            con.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (memory_id, obs_type, title, "", narrative, "", epoch),
            )
        con.commit()
        con.close()
        return db_path

    def test_prompts_only_returns_none_so_caller_falls_back_to_raw_jsonl(self) -> None:
        db_path = self.make_db(
            prompts=[("build the thing", 1000), ("/openacp-tunnel", 2000)],
            observations=[],
        )
        with mock.patch.object(worker, "CLAUDE_MEM_DB_PATH", db_path):
            result = worker.load_claude_mem_session("content-uuid")
        self.assertIsNone(
            result,
            "prompts-only claude-mem result must be rejected so the worker "
            "uses the richer raw jsonl instead of emitting empty summaries",
        )

    def test_observations_present_returns_merged_transcript(self) -> None:
        db_path = self.make_db(
            prompts=[("build the thing", 1000)],
            observations=[("feature", "Scaffolded project", "Created the dirs", 1500)],
        )
        with mock.patch.object(worker, "CLAUDE_MEM_DB_PATH", db_path):
            result = worker.load_claude_mem_session("content-uuid")
        self.assertIsNotNone(result)
        text, entries = result
        self.assertEqual(len(entries), 2)
        self.assertIn("build the thing", text)
        self.assertIn("Scaffolded project", text)


class ClaudeSchemaPayloadTests(unittest.TestCase):
    """``claude -p --output-format json`` wraps the schema object in a result
    envelope; the summarizer must read ``structured_output``, not the envelope.
    Reading the envelope directly is what left every weekly synthesis empty.
    """

    def test_extracts_structured_output_from_envelope(self) -> None:
        envelope = {
            "type": "result",
            "result": "Done.",
            "session_id": "x",
            "structured_output": {"summary": "did things", "completed_tasks": ["ran a test"]},
        }
        payload = worker._extract_claude_schema_payload(envelope)
        self.assertEqual(payload["summary"], "did things")
        self.assertEqual(payload["completed_tasks"], ["ran a test"])

    def test_accepts_bare_schema_object(self) -> None:
        bare = {"summary": "s", "completed_tasks": []}
        self.assertEqual(worker._extract_claude_schema_payload(bare), bare)

    def test_parses_json_string_in_result(self) -> None:
        envelope = {"type": "result", "result": json.dumps({"summary": "s", "decisions": ["d"]})}
        payload = worker._extract_claude_schema_payload(envelope)
        self.assertEqual(payload["decisions"], ["d"])

    def test_raises_on_error_envelope(self) -> None:
        with self.assertRaises(RuntimeError):
            worker._extract_claude_schema_payload(
                {"type": "result", "is_error": True, "result": "API Error", "structured_output": {}}
            )

    def test_raises_when_no_payload(self) -> None:
        with self.assertRaises(RuntimeError):
            worker._extract_claude_schema_payload({"type": "result", "result": "plain text"})


class ClaudeJsonlLocatorTests(unittest.TestCase):
    """``find_claude_session_jsonl`` must pick the session that was live at
    ``started_at`` — not the most-recent file. A short stub written after a
    long (possibly resumed) session ends must not shadow the real transcript.
    """

    def make_projects_dir(self) -> tuple[Path, Path]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        cwd = Path("/home/someone/work/proj")
        project_dir = root / wrapper.claude_project_slug_for_cwd(cwd)
        project_dir.mkdir(parents=True)
        return cwd, project_dir

    def write_session(
        self, project_dir: Path, name: str, first_iso: str, lines: int, mtime: float
    ) -> Path:
        path = project_dir / f"{name}.jsonl"
        rows = [json.dumps({"type": "summary"})]  # leading meta line, no timestamp
        rows.append(json.dumps({"type": "user", "timestamp": first_iso, "message": {"role": "user", "content": "hi"}}))
        for _ in range(max(0, lines - 1)):
            rows.append(json.dumps({"type": "assistant", "timestamp": first_iso, "message": {"role": "assistant", "content": "x"}}))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def test_resumed_session_wins_over_later_stub(self) -> None:
        cwd, project_dir = self.make_projects_dir()
        start = 1_780_494_302  # 2026-06-03T13:45:02Z
        # Real session: first event a day earlier (resumed), large, mtime after start.
        real = self.write_session(project_dir, "real", "2026-06-02T12:53:05Z", 2000, start + 40000)
        # Stub: created after the session ended, tiny, most-recent mtime.
        self.write_session(project_dir, "stub", "2026-06-04T00:44:27Z", 5, start + 43000)
        with mock.patch.object(wrapper, "CLAUDE_PROJECTS_DIR", project_dir.parent):
            picked = wrapper.find_claude_session_jsonl(cwd, start)
        self.assertEqual(picked, real)

    def test_falls_back_to_recent_when_no_window_contains_start(self) -> None:
        cwd, project_dir = self.make_projects_dir()
        start = 1_780_494_302
        # Both sessions start after `start`; none span it -> most-recent mtime wins.
        self.write_session(project_dir, "early", "2026-06-03T14:00:00Z", 10, start + 1000)
        late = self.write_session(project_dir, "late", "2026-06-03T15:00:00Z", 10, start + 5000)
        with mock.patch.object(wrapper, "CLAUDE_PROJECTS_DIR", project_dir.parent):
            picked = wrapper.find_claude_session_jsonl(cwd, start)
        self.assertEqual(picked, late)


PERIOD_PAYLOAD = {
    "narrative": "A productive stretch.",
    "work_and_projects": ["Shipped the moat verdict"],
    "how_you_think": ["Demean within-learner before claiming heterogeneity"],
    "working_with_me": ["Plain language; no spin"],
    "ideas_and_threads": ["Essay: are people unique?"],
    "open_threads": ["Is there an off-log moat?"],
    "evolution": ["Updated belief: no attempt-log moat"],
    "confidence": "high",
}


class PeriodSynthesisTests(unittest.TestCase):
    def make_root(self) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return Path(temp_dir.name)

    def write_intel(self, root: Path, date: str, sid: str, started: str) -> None:
        worker.write_json(
            root / "journal" / "sessions" / date / f"{sid}.intelligence.json",
            {"session_id": sid, "started_at": started, "active_agent": "builder",
             "active_mode": "application", "confidence": "high", "candidate_records": []},
        )

    # --- period ids ---
    def test_monthly_quarterly_id_basic(self) -> None:
        self.assertEqual(worker.monthly_id("2026-04-12T10:00:00+00:00"), "2026-04")
        self.assertEqual(worker.quarterly_id("2026-04-12T10:00:00+00:00"), "2026-Q2")
        self.assertEqual(worker.monthly_id("2026-06-04T10:00:00+00:00"), "2026-06")

    def test_quarterly_id_boundaries(self) -> None:
        self.assertEqual(worker.quarterly_id("2026-03-31T00:00:00+00:00"), "2026-Q1")
        self.assertEqual(worker.quarterly_id("2026-04-01T00:00:00+00:00"), "2026-Q2")
        self.assertEqual(worker.quarterly_id("2026-12-31T00:00:00+00:00"), "2026-Q4")
        self.assertEqual(worker.quarterly_id("2025-12-31T00:00:00+00:00"), "2025-Q4")

    def test_period_id_unknown_sentinels(self) -> None:
        self.assertEqual(worker.monthly_id("garbage"), "unknown-month")
        self.assertEqual(worker.quarterly_id(""), "unknown-quarter")

    # --- staleness ---
    def test_period_synthesis_stale(self) -> None:
        from datetime import datetime, timedelta, timezone
        root = self.make_root()
        path = root / "m.json"
        self.assertTrue(worker.period_synthesis_stale(path, 24))  # missing
        now = datetime.now(timezone.utc)
        worker.write_json(path, {"generated_at": (now - timedelta(hours=1)).isoformat()})
        self.assertFalse(worker.period_synthesis_stale(path, 24))  # fresh
        worker.write_json(path, {"generated_at": (now - timedelta(hours=30)).isoformat()})
        self.assertTrue(worker.period_synthesis_stale(path, 24))   # old
        self.assertTrue(worker.period_synthesis_stale(path, 0))    # threshold 0 always rebuilds

    def test_period_synthesis_stale_mtime_fallback(self) -> None:
        root = self.make_root()
        path = root / "m.json"
        worker.write_json(path, {"no_generated_at": True})
        old = path.stat().st_mtime - 30 * 3600
        os.utime(path, (old, old))
        self.assertTrue(worker.period_synthesis_stale(path, 24))

    # --- loaders ---
    def test_load_month_weeklies_filters_by_anchor(self) -> None:
        root = self.make_root()
        worker.write_json(root / "journal" / "weekly" / "2026-W17.json",
                          {"period_id": "2026-W17", "anchor_date": "2026-04-20T00:00:00+00:00"})
        worker.write_json(root / "journal" / "weekly" / "2026-W19.json",
                          {"period_id": "2026-W19", "anchor_date": "2026-05-05T00:00:00+00:00"})
        got = worker.load_month_weeklies(root, "2026-04")
        self.assertEqual([r["period_id"] for r in got], ["2026-W17"])

    def test_load_quarter_monthlies_filters_by_anchor(self) -> None:
        root = self.make_root()
        worker.write_json(root / "journal" / "monthly" / "2026-02.json",
                          {"period_id": "2026-02", "anchor_date": "2026-02-15T00:00:00+00:00"})
        worker.write_json(root / "journal" / "monthly" / "2026-05.json",
                          {"period_id": "2026-05", "anchor_date": "2026-05-10T00:00:00+00:00"})
        got = worker.load_quarter_monthlies(root, "2026-Q2")
        self.assertEqual([r["period_id"] for r in got], ["2026-05"])

    # --- render ---
    def test_render_period_synthesis_writes_md_and_json(self) -> None:
        root = self.make_root()
        with mock.patch.object(worker, "call_claude_period_synthesizer", return_value=dict(PERIOD_PAYLOAD)):
            rec = worker.render_period_synthesis(
                root, "month", "2026-06", "Month 2026-06", "2026-06-04T10:00:00+00:00",
                "rolled up input", source_count=4, footer_lines=["## Source Periods", "", "- x", ""])
        self.assertIsNotNone(rec)
        md = (root / "journal" / "monthly" / "2026-06.md").read_text(encoding="utf-8")
        for header in ("## Work & Projects", "## How You Think", "## Working With Me",
                       "## Ideas & Threads", "## Source Periods"):
            self.assertIn(header, md)
        self.assertIn("Shipped the moat verdict", md)
        self.assertIn("A productive stretch.", md)
        j = worker.read_json(root / "journal" / "monthly" / "2026-06.json")
        self.assertEqual(j["period_id"], "2026-06")
        self.assertTrue(j["anchor_date"].startswith("2026-06"))
        self.assertEqual(j["work_and_projects"], ["Shipped the moat verdict"])
        self.assertIn("generated_at", j)

    def test_render_period_synthesis_model_failure_returns_none(self) -> None:
        root = self.make_root()
        with mock.patch.object(worker, "call_claude_period_synthesizer", side_effect=RuntimeError("boom")), \
             mock.patch.object(worker, "call_codex_period_synthesizer", side_effect=RuntimeError("boom2")):
            rec = worker.render_period_synthesis(
                root, "month", "2026-07", "Month 2026-07", "2026-07-01T10:00:00+00:00", "in")
        self.assertIsNone(rec)
        self.assertFalse((root / "journal" / "monthly" / "2026-07.json").exists())

    def test_weekly_uses_model_axes_when_available(self) -> None:
        root = self.make_root()
        records = [{"session_id": "s1", "started_at": "2026-04-12T10:00:00+00:00",
                    "active_agent": "builder", "active_mode": "application", "confidence": "high",
                    "summary": "did work", "decisions": ["d1"], "candidate_records": []}]
        with mock.patch.object(worker, "call_claude_period_synthesizer", return_value=dict(PERIOD_PAYLOAD)):
            worker.render_weekly_synthesis(root, "2026-W15", records)
        md = (root / "journal" / "weekly" / "2026-W15.md").read_text(encoding="utf-8")
        self.assertIn("## Work & Projects", md)
        self.assertIn("## Sessions Included", md)
        self.assertIn("s1", md)
        self.assertTrue((root / "journal" / "weekly" / "2026-W15.json").exists())

    def test_period_synthesizer_reuses_envelope_extraction(self) -> None:
        import types
        root = self.make_root()
        envelope = {"type": "result", "result": "Done.",
                    "structured_output": dict(PERIOD_PAYLOAD)}
        fake = types.SimpleNamespace(stdout=json.dumps(envelope))
        with mock.patch.object(worker, "find_real_binary", return_value="claude"), \
             mock.patch.object(worker, "_format_period_prompt", return_value="PROMPT"), \
             mock.patch.object(worker.subprocess, "run", return_value=fake):
            payload = worker.call_claude_period_synthesizer(root, "week", "Week W", "input")
        self.assertEqual(payload["work_and_projects"], ["Shipped the moat verdict"])

    # --- worker ---
    def test_worker_all_history_enumeration_and_order(self) -> None:
        from tools.workers import synthesize_periods as synthmod
        root = self.make_root()
        self.write_intel(root, "2026-04-12", "a", "2026-04-12T10:00:00+00:00")
        self.write_intel(root, "2026-05-01", "b", "2026-05-01T10:00:00+00:00")
        calls: list[tuple[str, str]] = []

        def recorder(level):
            def _f(_root, period):
                calls.append((level, period))
                return True
            return _f

        with mock.patch.dict(synthmod.BUILDERS,
                             {"week": recorder("week"), "month": recorder("month"), "quarter": recorder("quarter")}):
            synthmod.main(["--root", str(root), "--level", "all", "--all-history", "--apply"])
        levels_in_order = [lvl for lvl, _ in calls]
        order_rank = {"week": 0, "month": 1, "quarter": 2}
        self.assertEqual(levels_in_order, sorted(levels_in_order, key=lambda l: order_rank[l]))
        self.assertIn(("month", "2026-04"), calls)
        self.assertIn(("month", "2026-05"), calls)
        self.assertIn(("quarter", "2026-Q2"), calls)

    def test_worker_dry_run_writes_nothing(self) -> None:
        from tools.workers import synthesize_periods as synthmod
        root = self.make_root()
        self.write_intel(root, "2026-04-12", "a", "2026-04-12T10:00:00+00:00")
        synthmod.main(["--root", str(root), "--level", "all", "--all-history"])
        self.assertFalse((root / "journal" / "weekly").exists())
        self.assertFalse((root / "journal" / "monthly").exists())


class StopHookCaptureTests(unittest.TestCase):
    """Spec §5 Capture: any session — wrapped or unwrapped — gets a synthesis
    pass, with session-id dedup so the wrapper path and the Stop hook never
    double-process the same Claude Code session."""

    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        for name in ("AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "README.md", "SKILLS.md"):
            write(root / name)
        (root / "system").mkdir(parents=True, exist_ok=True)
        write(root / "system" / "AGENT.md")
        (root / "domains").mkdir(parents=True, exist_ok=True)
        (root / "agents").mkdir(parents=True, exist_ok=True)
        return temp_dir, root

    def test_claim_session_id_is_idempotent(self) -> None:
        from tools.workers import session_hook

        _td, root = self.make_root()
        with _td:
            first = session_hook.claim_session_id(root, "abc-123", source="wrapper")
            second = session_hook.claim_session_id(root, "abc-123", source="stop-hook")
            other = session_hook.claim_session_id(root, "def-456", source="stop-hook")
            self.assertTrue(first, "first claim should win")
            self.assertFalse(second, "second claim of same id must lose")
            self.assertTrue(other, "a different id should be claimable")
            self.assertTrue(session_hook.is_session_processed(root, "abc-123"))
            self.assertTrue(session_hook.is_session_processed(root, "def-456"))
            self.assertFalse(session_hook.is_session_processed(root, "missing"))

    def test_scan_influence_tags_extracts_named_inputs_and_skips_placeholders(self) -> None:
        from tools.workers import session_hook

        _td, root = self.make_root()
        with _td:
            transcript = root / "native.jsonl"
            lines = [
                json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
                json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "text", "text": "Keeping it blunt [exo: applied feedback_plain_language — bans jargon]."},
                    {"type": "text", "text": "And [exo: applied project_x]. Example placeholder [exo: applied <name>] [exo: …]"},
                ]}}),
                json.dumps({"message": {"role": "assistant", "content": "dupe [exo: applied project_x]"}}),
            ]
            transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

            tags = session_hook.scan_influence_tags(transcript)
            self.assertIn("feedback_plain_language — bans jargon", tags)
            self.assertIn("project_x", tags)
            # placeholders/examples filtered out, and duplicates collapsed
            self.assertNotIn("<name>", tags)
            self.assertEqual(tags.count("project_x"), 1)
            self.assertTrue(all("<" not in t for t in tags))

    def test_scan_influence_tags_empty_when_no_transcript(self) -> None:
        from tools.workers import session_hook

        self.assertEqual(session_hook.scan_influence_tags(None), [])
        self.assertEqual(session_hook.scan_influence_tags(Path("/no/such/file.jsonl")), [])

    def test_stop_hook_records_influence_tags_in_manifest_and_reward_log(self) -> None:
        from tools.workers import session_hook, reward_log

        _td, root = self.make_root()
        with _td:
            transcript = root / "native.jsonl"
            transcript.write_text(
                json.dumps({"message": {"role": "assistant", "content":
                    "done [exo: applied project_exocortex_objective — instrument now]"}}) + "\n",
                encoding="utf-8",
            )

            captured: list[Path] = []

            def fake_worker(worker_root: Path, manifest_path: Path, timeout_seconds: int = 180) -> int:
                captured.append(manifest_path)
                return 0

            payload = {
                "session_id": "33333333-3333-3333-3333-333333333333",
                "cwd": str(root),
                "transcript_path": str(transcript),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(session_hook, "run_worker", fake_worker):
                result = session_hook.process_stop_hook(payload, root=root)

            self.assertEqual(result["status"], "dispatched")
            manifest = json.loads(captured[0].read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["influence_tags"],
                ["project_exocortex_objective — instrument now"],
            )
            # and the attribution signal lands in the reward log
            log_path = root / reward_log.REWARD_LOG_PATH
            self.assertTrue(log_path.exists())
            row = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
            self.assertEqual(row["source"], "influence-tags")
            self.assertEqual(row["influence_tags"], ["project_exocortex_objective — instrument now"])

    def test_stop_hook_triggers_worker_for_unclaimed_session(self) -> None:
        from tools.workers import session_hook

        _td, root = self.make_root()
        with _td:
            calls: list[Path] = []

            def fake_worker(worker_root: Path, manifest_path: Path, timeout_seconds: int = 180) -> int:
                calls.append(manifest_path)
                return 0

            payload = {
                "session_id": "11111111-1111-1111-1111-111111111111",
                "cwd": str(root),
                "transcript_path": str(root / "nope.jsonl"),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(session_hook, "run_worker", fake_worker):
                result = session_hook.process_stop_hook(payload, root=root)

            self.assertEqual(result["status"], "dispatched")
            self.assertEqual(len(calls), 1, "worker should run exactly once")
            manifest = json.loads(calls[0].read_text(encoding="utf-8"))
            self.assertEqual(manifest["source"], "stop-hook")
            self.assertEqual(manifest["claude_session_id"], payload["session_id"])
            self.assertEqual(manifest["capture_strategy"], "claude-jsonl")
            self.assertEqual(manifest["tool"], "claude")

    def test_stop_hook_refreshes_brief_independent_of_worker(self) -> None:
        """The "still no brief" failure: the Brief used to be rendered only at the
        tail of the heavy worker, which got killed on long sessions before
        reaching it, freezing the Brief. The hook now renders the Brief
        synchronously, independent of the (detached, fire-and-forget) worker — so
        even if the worker cannot be dispatched at all, the Brief still refreshes."""
        from tools.workers import session_hook, build_brief

        _td, root = self.make_root()
        with _td:
            def failed_dispatch(worker_root: Path, manifest_path: Path) -> int:
                return 1  # worst case: the worker could not even be spawned

            built: list[Path] = []

            def fake_write_brief(brief_root) -> None:
                built.append(Path(brief_root))
                inbox = Path(brief_root) / "journal" / "inbox"
                inbox.mkdir(parents=True, exist_ok=True)
                (inbox / "brief.md").write_text("fresh\n", encoding="utf-8")

            payload = {
                "session_id": "55555555-5555-5555-5555-555555555555",
                "cwd": str(root),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(session_hook, "run_worker", failed_dispatch), \
                 mock.patch.object(build_brief, "write_brief", fake_write_brief):
                result = session_hook.process_stop_hook(payload, root=root)

            self.assertEqual(result["status"], "error")  # dispatch failed...
            self.assertTrue(result["brief_refreshed"], "...but the Brief must still refresh")
            self.assertEqual(len(built), 1, "refresh_brief must call write_brief exactly once")
            self.assertTrue((root / "journal" / "inbox" / "brief.md").exists())

    def test_run_worker_dispatches_detached_and_returns_immediately(self) -> None:
        """The worker must be spawned detached (own session group, stdin closed,
        output redirected) and run_worker must return at once — never block the
        hook on the long synthesis chain, which is what got it timeout-killed."""
        from tools.workers import session_hook

        _td, root = self.make_root()
        with _td:
            captured: dict[str, Any] = {}

            class FakePopen:
                def __init__(self, cmd, **kw):
                    captured["cmd"] = cmd
                    captured["kw"] = kw

            with mock.patch.object(session_hook.subprocess, "Popen", FakePopen):
                code = session_hook.run_worker(root, root / "manifest.json")

            self.assertEqual(code, 0, "successful dispatch returns 0")
            self.assertTrue(captured["kw"].get("start_new_session"), "must detach into its own session")
            self.assertEqual(captured["kw"].get("stdin"), session_hook.subprocess.DEVNULL, "stdin must be closed")
            self.assertIn("process_session.py", " ".join(str(c) for c in captured["cmd"]))

    def test_stop_hook_dedups_against_already_processed_session(self) -> None:
        from tools.workers import session_hook

        _td, root = self.make_root()
        with _td:
            session_id = "22222222-2222-2222-2222-222222222222"
            session_hook.claim_session_id(root, session_id, source="wrapper")

            calls: list[Path] = []

            def fake_worker(worker_root: Path, manifest_path: Path, timeout_seconds: int = 180) -> int:
                calls.append(manifest_path)
                return 0

            payload = {"session_id": session_id, "cwd": str(root), "hook_event_name": "Stop"}
            with mock.patch.object(session_hook, "run_worker", fake_worker):
                result = session_hook.process_stop_hook(payload, root=root)

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(calls, [], "worker must not run for an already-processed session")

    def test_stop_hook_double_fire_processes_once(self) -> None:
        from tools.workers import session_hook

        _td, root = self.make_root()
        with _td:
            calls: list[Path] = []

            def fake_worker(worker_root: Path, manifest_path: Path, timeout_seconds: int = 180) -> int:
                calls.append(manifest_path)
                return 0

            payload = {
                "session_id": "33333333-3333-3333-3333-333333333333",
                "cwd": str(root),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(session_hook, "run_worker", fake_worker):
                first = session_hook.process_stop_hook(payload, root=root)
                second = session_hook.process_stop_hook(payload, root=root)

            self.assertEqual(first["status"], "dispatched")
            self.assertEqual(second["status"], "skipped")
            self.assertEqual(len(calls), 1, "two Stop-hook fires must process the session once")

    def test_stop_hook_never_raises_on_bad_payload(self) -> None:
        from tools.workers import session_hook

        _td, root = self.make_root()
        with _td:
            self.assertEqual(session_hook.process_stop_hook({}, root=root)["status"], "noop")
            with mock.patch.object(
                session_hook, "run_worker", side_effect=RuntimeError("boom")
            ):
                result = session_hook.process_stop_hook(
                    {"session_id": "44444444-4444-4444-4444-444444444444", "cwd": str(root)},
                    root=root,
                )
            self.assertEqual(result["status"], "error")

    def test_stop_hook_entrypoint_exits_zero(self) -> None:
        """The CLI entrypoint must exit 0 even when stdin is garbage, so a hook
        failure can never interrupt the session."""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "workers" / "session_hook.py")],
            input="not json at all",
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=dict(os.environ),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class ObserverHeadlessCaptureFilterTests(unittest.TestCase):
    """Spec §5 item 1b: claude-mem observer subprocesses and other
    headless/non-interactive `claude -p` / `stream-json` invocations must not be
    captured or processed. Real interactive human sessions still capture.
    """

    # The exact argv claude-mem launches its observer subprocesses with — a
    # headless stream-json round-trip with no real terminal.
    OBSERVER_ARGV = [
        "--output-format", "stream-json", "--verbose",
        "--input-format", "stream-json",
        "--model", "claude-sonnet-4-6",
        "--permission-prompt-tool", "stdio",
        "--permission-mode", "dontAsk",
    ]

    def test_observer_cwd_is_not_captured(self) -> None:
        observer_cwd = Path.home() / ".claude-mem" / "observer-sessions"
        self.assertTrue(
            wrapper.is_observer_or_headless_session(
                "claude", self.OBSERVER_ARGV, observer_cwd, stdin_is_tty=False
            )
        )
        # Even with a (spoofed) tty, the cwd alone is disqualifying.
        self.assertTrue(
            wrapper.is_observer_or_headless_session(
                "claude", [], observer_cwd, stdin_is_tty=True
            )
        )

    def test_claude_mem_internal_dir_is_not_captured(self) -> None:
        internal = Path.home() / ".claude-mem" / "anything-else"
        self.assertTrue(
            wrapper.is_observer_or_headless_session(
                "claude", [], internal, stdin_is_tty=True
            )
        )

    def test_headless_print_flag_is_not_captured(self) -> None:
        for argv in (["-p", "do a thing"], ["--print", "do a thing"]):
            self.assertTrue(
                wrapper.is_observer_or_headless_session(
                    "claude", argv, Path("/tmp/project"), stdin_is_tty=True
                ),
                argv,
            )

    def test_stream_json_format_is_not_captured(self) -> None:
        argv = ["--input-format", "stream-json", "--output-format", "stream-json"]
        self.assertTrue(
            wrapper.is_observer_or_headless_session(
                "claude", argv, Path("/tmp/project"), stdin_is_tty=True
            )
        )

    def test_non_tty_session_is_not_captured(self) -> None:
        self.assertTrue(
            wrapper.is_observer_or_headless_session(
                "claude", [], Path("/tmp/project"), stdin_is_tty=False
            )
        )

    def test_interactive_human_session_is_captured(self) -> None:
        self.assertFalse(
            wrapper.is_observer_or_headless_session(
                "claude", [], Path("/tmp/project"), stdin_is_tty=True
            )
        )
        self.assertFalse(
            wrapper.is_observer_or_headless_session(
                "claude", ["--model", "opus"], Path("/tmp/project"), stdin_is_tty=True
            )
        )

    def test_should_capture_session_rejects_observer_argv(self) -> None:
        # An interactive tty + injection-eligible argv would normally capture;
        # the headless stream-json signature must override that.
        self.assertFalse(
            wrapper.should_capture_session("claude", self.OBSERVER_ARGV, True)
        )

    def test_should_capture_session_keeps_interactive(self) -> None:
        # Plain interactive `claude` on a tty still captures.
        with mock.patch.object(wrapper.os, "getcwd", return_value="/tmp/project"):
            self.assertTrue(wrapper.should_capture_session("claude", [], True))

    def test_stop_hook_skips_observer_cwd(self) -> None:
        from tools.workers import session_hook

        _td, root = StopHookCaptureTests.make_root(self)
        with _td:
            calls: list[Path] = []

            def fake_worker(worker_root: Path, manifest_path: Path, timeout_seconds: int = 180) -> int:
                calls.append(manifest_path)
                return 0

            payload = {
                "session_id": "55555555-5555-5555-5555-555555555555",
                "cwd": str(Path.home() / ".claude-mem" / "observer-sessions"),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(session_hook, "run_worker", fake_worker):
                result = session_hook.process_stop_hook(payload, root=root)

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(calls, [], "observer-session Stop hook must not run the worker")

    def test_stop_hook_skips_sdk_cli_entrypoint(self) -> None:
        from tools.workers import session_hook

        _td, root = StopHookCaptureTests.make_root(self)
        with _td:
            transcript = root / "sdk-cli.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "entrypoint": "sdk-cli",
                        "message": {"role": "user", "content": "headless print prompt"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            calls: list[Path] = []

            def fake_worker(worker_root: Path, manifest_path: Path, timeout_seconds: int = 180) -> int:
                calls.append(manifest_path)
                return 0

            payload = {
                "session_id": "77777777-7777-7777-7777-777777777777",
                "cwd": str(root),
                "transcript_path": str(transcript),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(session_hook, "run_worker", fake_worker):
                result = session_hook.process_stop_hook(payload, root=root)

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result.get("reason"), "observer/headless session")
            self.assertEqual(calls, [], "sdk-cli Stop hook must not run the worker")

    def test_stop_hook_still_processes_cli_entrypoint_session(self) -> None:
        from tools.workers import session_hook

        _td, root = StopHookCaptureTests.make_root(self)
        with _td:
            transcript = root / "interactive.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "entrypoint": "cli",
                        "message": {"role": "user", "content": "fix the wrapper"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            calls: list[Path] = []

            def fake_worker(worker_root: Path, manifest_path: Path, timeout_seconds: int = 180) -> int:
                calls.append(manifest_path)
                return 0

            payload = {
                "session_id": "88888888-8888-8888-8888-888888888888",
                "cwd": str(root),
                "transcript_path": str(transcript),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(session_hook, "run_worker", fake_worker):
                result = session_hook.process_stop_hook(payload, root=root)

            self.assertEqual(result["status"], "dispatched")
            self.assertEqual(len(calls), 1, "interactive cli entrypoint must still dispatch the worker")

    def test_stop_hook_still_processes_real_session(self) -> None:
        from tools.workers import session_hook

        _td, root = StopHookCaptureTests.make_root(self)
        with _td:
            calls: list[Path] = []

            def fake_worker(worker_root: Path, manifest_path: Path, timeout_seconds: int = 180) -> int:
                calls.append(manifest_path)
                return 0

            payload = {
                "session_id": "66666666-6666-6666-6666-666666666666",
                "cwd": str(root),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(session_hook, "run_worker", fake_worker):
                result = session_hook.process_stop_hook(payload, root=root)

            self.assertEqual(result["status"], "dispatched")
            self.assertEqual(len(calls), 1)


class QuarantineStuckBacklogTests(unittest.TestCase):
    """Spec §5 item 1b: a bounded, reversible, logged way to clear manifests
    stuck at `summary_status: processing` so they stop counting as missing
    artifacts. Dry-run by default; `--apply` required to mutate."""

    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        return temp_dir, root

    def write_manifest(self, root: Path, date: str, sid: str, *, status: str, cwd: str) -> Path:
        session_dir = root / "journal" / "sessions" / date
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / f"{sid}.json"
        path.write_text(
            json.dumps({"session_id": sid, "summary_status": status, "cwd": cwd}),
            encoding="utf-8",
        )
        return path

    def test_dry_run_reports_but_does_not_mutate(self) -> None:
        from tools.workers import reprocess_sessions as rs

        _td, root = self.make_root()
        with _td:
            obs = self.write_manifest(
                root, "2026-06-20", "a" * 8, status="processing",
                cwd=str(Path.home() / ".claude-mem" / "observer-sessions"),
            )
            targets = rs.stuck_processing_manifests(root)
            self.assertIn(obs, targets)
            # Dry-run must not change status.
            rs.quarantine_stuck(root, targets, apply=False)
            self.assertEqual(
                json.loads(obs.read_text())["summary_status"], "processing"
            )

    def test_apply_quarantines_and_logs(self) -> None:
        from tools.workers import reprocess_sessions as rs

        _td, root = self.make_root()
        with _td:
            obs = self.write_manifest(
                root, "2026-06-20", "b" * 8, status="processing",
                cwd=str(Path.home() / ".claude-mem" / "observer-sessions"),
            )
            targets = rs.stuck_processing_manifests(root)
            changed = rs.quarantine_stuck(root, targets, apply=True)
            self.assertEqual(changed, 1)
            data = json.loads(obs.read_text())
            self.assertEqual(data["summary_status"], "quarantined")
            # Reversible: original status preserved.
            self.assertEqual(data.get("prior_summary_status"), "processing")
            # Logged.
            ledger = root / "journal" / "inbox" / "quarantine-log.md"
            self.assertTrue(ledger.exists())
            self.assertIn("quarantined", ledger.read_text())

    def test_bounded_by_limit(self) -> None:
        from tools.workers import reprocess_sessions as rs

        _td, root = self.make_root()
        with _td:
            for i in range(5):
                self.write_manifest(
                    root, "2026-06-20", f"c{i}" + "0" * 6, status="processing",
                    cwd=str(Path.home() / ".claude-mem" / "observer-sessions"),
                )
            targets = rs.stuck_processing_manifests(root, limit=2)
            self.assertEqual(len(targets), 2)

    def test_complete_sessions_are_not_quarantined(self) -> None:
        from tools.workers import reprocess_sessions as rs

        _td, root = self.make_root()
        with _td:
            done = self.write_manifest(
                root, "2026-06-20", "d" * 8, status="complete",
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            targets = rs.stuck_processing_manifests(root)
            self.assertNotIn(done, targets)


class HealthMissingArtifactsExcludesQuarantineTests(unittest.TestCase):
    """Spec §3/§5 item 1b: quarantined or skipped manifests legitimately have no
    summary/candidate artifacts and must not count as "missing artifacts" in the
    health metric. They are reported separately."""

    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        return temp_dir, Path(temp_dir.name)

    def write_manifest(self, root: Path, sid: str, status: str) -> Path:
        session_dir = root / "journal" / "sessions" / "2026-06-20"
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / f"{sid}.json"
        path.write_text(json.dumps({"session_id": sid, "summary_status": status}), encoding="utf-8")
        return path

    def test_quarantined_manifest_not_counted_missing(self) -> None:
        from tools.workers import reprocess_sessions as rs

        _td, root = self.make_root()
        with _td:
            quarantined = self.write_manifest(root, "q" * 8, "quarantined")
            processing = self.write_manifest(root, "p" * 8, "processing")
            missing = rs.missing_session_manifests(root)
            self.assertIn(processing, missing)
            self.assertNotIn(quarantined, missing)
            self.assertEqual(rs.excluded_from_missing_count(root), 1)

    def test_skipped_manifest_not_counted_missing(self) -> None:
        from tools.workers import reprocess_sessions as rs

        _td, root = self.make_root()
        with _td:
            skipped = self.write_manifest(root, "s" * 8, "skipped")
            self.assertNotIn(skipped, rs.missing_session_manifests(root))
            self.assertEqual(rs.excluded_from_missing_count(root), 1)

    def test_health_reports_quarantined_separately(self) -> None:
        from tools.workers import health_check

        _td, root = self.make_root()
        with _td:
            self.write_manifest(root, "q" * 8, "quarantined")
            self.write_manifest(root, "p" * 8, "processing")
            items = {item.name: item for item in health_check.build_health(root)}
            detail = items["session_artifacts"].detail
            self.assertIn("1 of latest 100 manifests missing artifacts", detail)
            self.assertIn("1 quarantined/skipped excluded", detail)


class PromotionRouterNeverAutoAppliesTests(unittest.TestCase):
    """Spec §8 locked decision: the auto-apply tier is removed. The router may
    classify surface_now vs queue but must never durably promote."""

    def test_auto_apply_helpers_are_gone(self) -> None:
        self.assertFalse(hasattr(worker, "append_auto_apply"))
        self.assertFalse(hasattr(worker, "low_risk_auto_apply_candidate"))

    def test_promotion_tier_only_returns_surface_now_or_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # A high-confidence, high-evidence memory candidate would previously
            # have been auto-applied; it must now stage in the queue.
            record = {
                "candidate_type": "memory",
                "text": "the user prefers direct, structured answers over hedging.",
                "confidence": "high",
                "evidence_count": 9,
                "contradicts": [],
                "suggested_destination": "MEMORY.md",
                "normalized_key": "the-user prefers direct structured answers over hedging",
            }
            tier = worker.promotion_tier(root, record)
            self.assertIn(tier, {"surface_now", "queue"})
            self.assertNotEqual(tier, "auto_apply")
            self.assertEqual(tier, "queue")

    def test_route_does_not_write_durable_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "MEMORY.md"
            destination.write_text("# Memory\n\n- existing line\n", encoding="utf-8")
            before = destination.read_text(encoding="utf-8")
            record = {
                "candidate_type": "memory",
                "text": "the user prefers direct, structured answers over hedging.",
                "confidence": "high",
                "evidence_count": 9,
                "contradicts": [],
                "suggested_destination": "MEMORY.md",
                "normalized_key": "the-user prefers direct structured answers over hedging",
                "why_it_matters": "x",
                "justification": "x",
                "related_focus": [],
                "source_session_ids": ["sess-1"],
            }
            manifest = {"session_id": "sess-1", "started_at": "2026-06-21T10:00:00+00:00"}
            worker.route_current_promotions(root, manifest, [record], [record])
            # Durable file untouched; no auto-applied ledger created.
            self.assertEqual(destination.read_text(encoding="utf-8"), before)
            self.assertFalse((root / "journal" / "inbox" / "auto-applied.md").exists())
            self.assertEqual(record["tier"], "queue")


if __name__ == "__main__":
    unittest.main()
