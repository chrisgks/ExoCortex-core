import tempfile
import unittest
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_PATH = ROOT / "tools" / "mission-control" / "backend" / "main.py"
SPEC = importlib.util.spec_from_file_location("mission_control_backend", MODULE_PATH)
backend = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
try:
    SPEC.loader.exec_module(backend)
except ModuleNotFoundError as exc:
    if exc.name == "fastapi":
        raise unittest.SkipTest("Mission Control backend tests require the optional fastapi dependency.")
    raise


class MissionControlBackendTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        for name in ("AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "README.md", "SKILLS.md"):
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("# test\n", encoding="utf-8")
        for name in ("AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "README.md", "SKILLS.md", "OPEN LOOPS.md", "PRIORITIES.md"):
            (root / "system" / name).parent.mkdir(parents=True, exist_ok=True)
            (root / "system" / name).write_text("# test\n", encoding="utf-8")
        (root / "journal" / "inbox").mkdir(parents=True, exist_ok=True)
        (root / "journal" / "inbox" / "pending-intents.md").write_text("# Pending Intent Candidates\n", encoding="utf-8")
        (root / "agents" / "builder").mkdir(parents=True, exist_ok=True)
        (root / "agents" / "planning").mkdir(parents=True, exist_ok=True)
        (root / "agents" / "research").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "inbox-triage").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "inbox-triage" / "SKILL.md").write_text("# skill\n", encoding="utf-8")
        project_root = root / "domains" / "work" / "projects" / "demo"
        for name in ("README.md", "AGENT.md", "MEMORY.md", "STATE.md", "WORKFLOWS.md", "SKILLS.md"):
            (project_root / name).parent.mkdir(parents=True, exist_ok=True)
            if name == "SKILLS.md":
                (project_root / name).write_text("- `skills/inbox-triage/`\n", encoding="utf-8")
            else:
                (project_root / name).write_text("# test\n", encoding="utf-8")
        return temp_dir, root

    def test_parse_queue_candidates_reads_structured_blocks(self) -> None:
        content = """# Pending Intent Candidates

Promotion ladder: `candidate` -> `inferred_intent` -> `confirmed_open_loop` -> `priority`.

## Ready To Confirm As Open Loops

### We will later automate recurring inbox triage through cron jobs.

- signal_ladder: `repeated_pattern`
- evidence_count: `2`
- confidence: `medium`
- suggested_destination: `system/OPEN LOOPS.md`
- artifact_kind: `open_loop`
- intent_stage: `inferred_intent`
- review_recommendation: `confirm_open_loop`
"""
        candidates = backend.parse_queue_candidates("pending-intents.md", content)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].type, "intent")
        self.assertEqual(candidates[0].content, "We will later automate recurring inbox triage through cron jobs.")
        self.assertEqual(candidates[0].suggested_destination, "system/OPEN LOOPS.md")
        self.assertEqual(candidates[0].review_recommendation, "confirm_open_loop")
        self.assertEqual(candidates[0].queue_section, "Ready To Confirm As Open Loops")

    def test_remove_candidate_block_deletes_only_selected_block(self) -> None:
        initial = """# Pending Memory Candidates

## Memory

### Prefer direct answers.

- signal_ladder: `repeated_pattern`
- evidence_count: `2`

### Preserve chronology on source pages.

- signal_ladder: `candidate`
- evidence_count: `1`
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "pending-memory.md"
            path.write_text(initial, encoding="utf-8")
            backend.remove_candidate_block(
                path,
                backend.Candidate(
                    id="pending-memory.md:5",
                    type="memory",
                    content="Prefer direct answers.",
                    evidence_count=2,
                    source_file="pending-memory.md",
                    block_start_line=5,
                ),
            )

            updated = path.read_text(encoding="utf-8")
            self.assertNotIn("### Prefer direct answers.", updated)
            self.assertIn("### Preserve chronology on source pages.", updated)

    def test_build_action_space_returns_context_policy_action_graph(self) -> None:
        temp_dir, root = self.make_root()
        self.addCleanup(temp_dir.cleanup)

        payload = backend.build_action_space(root, "domains/work/projects/demo")

        self.assertEqual(payload["center"]["path"], "domains/work/projects/demo")
        self.assertEqual(payload["center"]["agent"], "planning")
        node_ids = {node["id"] for node in payload["nodes"]}
        self.assertIn("context:domains/work/projects/demo", node_ids)
        self.assertIn("policy:agent:planning", node_ids)
        self.assertIn("action:move_context", node_ids)
        self.assertIn("target:user", node_ids)
        self.assertTrue(
            any(node.get("group") == "skill" and node["subtitle"] == "skills/inbox-triage" for node in payload["nodes"])
        )
        self.assertTrue(
            any(edge["from"] == "action:promote_signal" and edge["to"].startswith("target:promotion:") for edge in payload["edges"])
        )


if __name__ == "__main__":
    unittest.main()
