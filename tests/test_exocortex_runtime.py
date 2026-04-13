import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workers import intent_review
from tools.workers import process_session as worker
from tools.wrappers import doctor
from tools.wrappers import exocortex_wrapper as wrapper


def write(path: Path, content: str = "# test\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
        self.assertEqual(wrapper.inject_args("gemini", [], "PROMPT", stdin_is_tty=False), [])

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

        worker.render_weekly_synthesis(root, "2026-W15", intelligence)
        weekly = (root / "journal" / "weekly" / "2026-W15.md").read_text(encoding="utf-8")
        self.assertIn("Repeated High-Signal Patterns", weekly)
        self.assertIn("Turn repeated planning steps into a checklist.", weekly)


if __name__ == "__main__":
    unittest.main()
