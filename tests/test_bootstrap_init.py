import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bootstrap import init as bootstrap


def write(path: Path, content: str = "# test\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class BootstrapInitTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        write(root / "AGENT.md")
        write(root / "README.md")
        (root / "agents").mkdir()
        (root / "domains").mkdir()
        (root / "system").mkdir()
        (root / "tools" / "wrappers").mkdir(parents=True)
        write(root / "tools" / "wrappers" / "install.sh", "#!/bin/zsh\n")
        return temp_dir, root

    def test_find_repo_root_walks_upward(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        nested = root / "domains" / "work" / "projects"
        nested.mkdir(parents=True)

        detected = bootstrap.find_repo_root(nested)
        self.assertEqual(detected, root.resolve())

    def test_repo_init_creates_runtime_scaffold(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        summary = bootstrap.ensure_repo_runtime(root, force=False)

        self.assertTrue((root / "journal" / "inbox" / "pending-intents.md").exists())
        self.assertTrue((root / "raw" / "inbox" / "concept_seeds" / "README.md").exists())
        self.assertTrue((root / "wiki" / "00_meta" / "Backlog.md").exists())
        self.assertGreater(len(summary.created), 0)

    def test_domain_init_substitutes_name_and_title(self) -> None:
        # Templates live in tools/bootstrap/templates/ — use real templates and
        # verify substitution rather than matching stub content.
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        bootstrap.init_domain(root, "research-lab", force=False)

        readme = (root / "domains" / "research-lab" / "README.md").read_text(encoding="utf-8")
        agent = (root / "domains" / "research-lab" / "AGENT.md").read_text(encoding="utf-8")

        # Template placeholders must be replaced; raw placeholder text must not appear.
        self.assertNotIn("{{DOMAIN_NAME}}", readme)
        self.assertNotIn("{{DOMAIN_TITLE}}", readme)
        self.assertNotIn("{{DOMAIN_NAME}}", agent)
        # Substituted values must appear.
        self.assertIn("Research Lab", readme)
        self.assertIn("research-lab", agent)

    def test_project_init_creates_static_identity_files(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        bootstrap.init_project(root, "work", "alpha-beta", force=False)

        project_root = root / "domains" / "work" / "projects" / "alpha-beta"
        self.assertTrue((project_root / "README.md").exists())
        self.assertTrue((project_root / "AGENT.md").exists())

        readme = (project_root / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("{{PROJECT_TITLE}}", readme)
        self.assertNotIn("{{PROJECT_NAME}}", readme)
        self.assertIn("Alpha Beta", readme)

    def test_project_init_is_lazy_no_empty_state_stubs(self) -> None:
        # Lazy contracts: scaffolding seeds only the
        # static-identity files. No empty STATE/MEMORY/WORKFLOWS stubs, and
        # WORKFLOWS.md is retired entirely.
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        bootstrap.init_project(root, "work", "alpha-beta", force=False)

        project_root = root / "domains" / "work" / "projects" / "alpha-beta"
        self.assertFalse((project_root / "STATE.md").exists())
        self.assertFalse((project_root / "MEMORY.md").exists())
        self.assertFalse((project_root / "WORKFLOWS.md").exists())

    def test_project_init_creates_full_wiki_structure(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        bootstrap.init_project(root, "work", "my-project", force=False)

        wiki = root / "domains" / "work" / "projects" / "my-project" / "wiki"
        expected = [
            wiki / "index.md",
            wiki / "log.md",
            wiki / "00_meta" / "Scope.md",
            wiki / "00_meta" / "Operating Contract.md",
            wiki / "01_overviews" / "README.md",
            wiki / "03_concepts" / "README.md",
            wiki / "04_analyses" / "README.md",
            wiki / "05_sources" / "README.md",
        ]
        for path in expected:
            with self.subTest(path=path.relative_to(wiki)):
                self.assertTrue(path.exists(), f"Missing: {path.relative_to(wiki)}")

    def test_project_init_wiki_templates_substitute_project_name(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        bootstrap.init_project(root, "work", "my-project", force=False)

        wiki = root / "domains" / "work" / "projects" / "my-project" / "wiki"
        for path in (wiki / "index.md", wiki / "log.md", wiki / "00_meta" / "Scope.md"):
            with self.subTest(path=path.relative_to(wiki)):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn("{{PROJECT_NAME}}", content)
                self.assertNotIn("{{PROJECT_TITLE}}", content)
                self.assertNotIn("{{DOMAIN_NAME}}", content)

    def test_project_init_wiki_operating_contract_references_root(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        bootstrap.init_project(root, "work", "my-project", force=False)

        contract = (
            root / "domains" / "work" / "projects" / "my-project"
            / "wiki" / "00_meta" / "Operating Contract.md"
        ).read_text(encoding="utf-8")
        # Must reference the root contract so the inheritance chain is clear.
        self.assertIn("wiki/00_meta/Operating Contract.md", contract)

    def test_project_init_sources_at_05_not_bare_sources(self) -> None:
        # Regression: sources used to be scaffolded at wiki/sources/ instead of wiki/05_sources/.
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        bootstrap.init_project(root, "work", "my-project", force=False)

        wiki = root / "domains" / "work" / "projects" / "my-project" / "wiki"
        self.assertTrue((wiki / "05_sources" / "README.md").exists())
        self.assertFalse((wiki / "sources" / "README.md").exists())

    def test_init_instance_creates_runnable_clean_slate(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        target = Path(temp_dir.name) / "fresh-instance"

        summary = bootstrap.init_instance(target, force=False)

        # Static-identity contracts.
        self.assertTrue((target / "README.md").exists())
        self.assertTrue((target / "AGENT.md").exists())
        self.assertTrue((target / "system" / "README.md").exists())
        self.assertTrue((target / "system" / "AGENT.md").exists())
        # Clean-slate scaffolds + empty inbox.
        self.assertTrue((target / "journal" / "inbox" / "README.md").exists())
        self.assertTrue((target / "raw" / "inbox" / "concept_seeds" / "README.md").exists())
        self.assertTrue((target / "wiki" / "index.md").exists())
        # Structural marker dirs the engine resolves an instance by.
        self.assertTrue((target / "agents").is_dir())
        self.assertTrue((target / "domains").is_dir())
        self.assertGreater(len(summary.created), 0)

    def test_init_instance_is_lazy_no_empty_state_stubs(self) -> None:
        # Lazy contracts: no empty MEMORY/STATE/WORKFLOWS.
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        target = Path(temp_dir.name) / "fresh-instance"

        bootstrap.init_instance(target, force=False)

        self.assertFalse((target / "MEMORY.md").exists())
        self.assertFalse((target / "STATE.md").exists())
        self.assertFalse((target / "WORKFLOWS.md").exists())

    def test_init_instance_resolves_as_instance_root(self) -> None:
        # A freshly-initialised instance must be recognised by the resolver.
        from tools import instance as instance_mod

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        target = Path(temp_dir.name) / "fresh-instance"
        bootstrap.init_instance(target, force=False)

        self.assertTrue(instance_mod._looks_like_instance(target))

    def test_install_wrappers_invokes_wrapper_installer(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        with patch("tools.bootstrap.init.subprocess.run") as run:
            bootstrap.install_wrappers(root, None)

        run.assert_called_once_with([str(root / "tools" / "wrappers" / "install.sh")], check=True)

    def test_install_cron_invokes_cron_installer(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        (root / "tools" / "automations").mkdir(parents=True)
        write(root / "tools" / "automations" / "install_cron.sh", "#!/bin/zsh\n")

        with patch("tools.bootstrap.init.subprocess.run") as run:
            bootstrap.install_cron(root)

        run.assert_called_once_with([str(root / "tools" / "automations" / "install_cron.sh")], check=True)
