import sys
import tempfile
import unittest
from pathlib import Path

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
        (root / "templates" / "domain-context").mkdir(parents=True)
        (root / "templates" / "project-context").mkdir(parents=True)
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

    def test_domain_init_renders_templates(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "templates" / "domain-context" / "README.template.md", "# {{DOMAIN_TITLE}}\n")
        write(root / "templates" / "domain-context" / "AGENT.template.md", "agent {{DOMAIN_NAME}}\n")
        write(root / "templates" / "domain-context" / "projects.README.template.md", "projects {{DOMAIN_TITLE}}\n")

        bootstrap.init_domain(root, "research-lab", force=False)

        self.assertEqual((root / "domains" / "research-lab" / "README.md").read_text(encoding="utf-8"), "# Research Lab\n")
        self.assertEqual((root / "domains" / "research-lab" / "AGENT.md").read_text(encoding="utf-8"), "agent research-lab\n")

    def test_project_init_creates_domain_and_project_files(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        write(root / "templates" / "domain-context" / "README.template.md", "# {{DOMAIN_TITLE}}\n")
        write(root / "templates" / "domain-context" / "AGENT.template.md", "agent {{DOMAIN_NAME}}\n")
        write(root / "templates" / "domain-context" / "projects.README.template.md", "projects {{DOMAIN_TITLE}}\n")
        write(root / "templates" / "project-context" / "README.template.md", "# {{PROJECT_TITLE}}\n")
        write(root / "templates" / "project-context" / "AGENT.template.md", "agent {{PROJECT_NAME}}\n")
        write(root / "templates" / "project-context" / "MEMORY.template.md", "memory {{PROJECT_TITLE}}\n")
        write(root / "templates" / "project-context" / "STATE.template.md", "state {{PROJECT_TITLE}}\n")
        write(root / "templates" / "project-context" / "WORKFLOWS.template.md", "workflow {{PROJECT_TITLE}}\n")

        bootstrap.init_project(root, "work", "alpha-beta", force=False)

        project_root = root / "domains" / "work" / "projects" / "alpha-beta"
        self.assertTrue((project_root / "README.md").exists())
        self.assertEqual((project_root / "STATE.md").read_text(encoding="utf-8"), "state Alpha Beta\n")
